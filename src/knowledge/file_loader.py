"""Download Slack files safely and extract text for staged ingest."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".log", ".json", ".yml", ".yaml"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_EXTENSIONS = SUPPORTED_TEXT_EXTENSIONS | {".pdf", ".docx"} | IMAGE_EXTENSIONS
SLACK_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_SLACK_FILE_REDIRECTS = 5


class UploadValidationError(ValueError):
    """Raised when an uploaded file fails intake policy."""


def validate_slack_file(
    file_info: dict[str, Any],
    *,
    max_bytes: int | None = None,
) -> tuple[str, str]:
    """Validate upload metadata and return safe filename + extension."""
    name = str(file_info.get("name") or "").strip()
    if not name:
        raise UploadValidationError("File has no name")
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UploadValidationError(f"Unsupported file type: {suffix or '(none)'}")
    limit = int(max_bytes if max_bytes is not None else settings.max_upload_bytes)
    size = file_info.get("size")
    if size is not None and int(size) > limit:
        raise UploadValidationError("File exceeds the configured upload-size limit")

    file_id = str(file_info.get("id") or "file")
    stem = "".join(c for c in Path(name).stem if c.isalnum() or c in ("-", "_", " ")).strip()
    stem = stem[:120] or "file"
    safe_name = f"{file_id}_{stem}{suffix}"
    return safe_name, suffix


def _reject_html_masquerading_as_file(path: Path) -> None:
    """Catch Slack auth/permission pages returned with a successful HTTP response."""
    prefix = path.read_bytes()[:1024].lstrip().lower()
    if prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html"):
        raise UploadValidationError(
            "Slack returned an HTML page instead of the attachment. "
            "Reinstall the Slack app with the `files:read` bot scope and retry."
        )


def _trusted_slack_download_url(url: str) -> bool:
    """Only allow authenticated file redirects to HTTPS hosts owned by slack.com."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and (host == "slack.com" or host.endswith(".slack.com"))


async def download_slack_file(
    file_info: dict[str, Any],
    target_dir: Path | None = None,
    *,
    max_bytes: int | None = None,
    timeout_seconds: float = 60.0,
) -> Path:
    """Stream a validated Slack file to the controlled staging directory.

    Slack may redirect a private file from ``files.slack.com`` to the workspace's
    ``*.slack.com`` host. HTTP clients intentionally strip Authorization on a
    cross-host redirect, so redirects are followed manually and the bot bearer
    token is preserved only while the destination remains a trusted Slack HTTPS
    host.
    """
    url = file_info.get("url_private_download") or file_info.get("url_private")
    if not url:
        raise UploadValidationError("Slack file object has no downloadable URL")

    current_url = str(url)
    if not _trusted_slack_download_url(current_url):
        raise UploadValidationError("Slack file URL is not on a trusted Slack host")

    limit = int(max_bytes if max_bytes is not None else settings.max_upload_bytes)
    safe_name, _ = validate_slack_file(file_info, max_bytes=limit)
    directory = Path(target_dir or settings.staging_path)
    directory.mkdir(parents=True, exist_ok=True)
    target_path = (directory / safe_name).resolve()
    if directory.resolve() not in target_path.parents:
        raise UploadValidationError("Unsafe download path")

    headers = {"Authorization": f"Bearer {settings.slack_bot_token}"}
    written = 0
    try:
        async with httpx.AsyncClient(timeout=float(timeout_seconds), follow_redirects=False) as client:
            for redirect_count in range(MAX_SLACK_FILE_REDIRECTS + 1):
                async with client.stream("GET", current_url, headers=headers) as resp:
                    if resp.status_code in SLACK_REDIRECT_STATUSES:
                        location = resp.headers.get("location")
                        if not location:
                            raise UploadValidationError("Slack file redirect did not include a destination")
                        if redirect_count >= MAX_SLACK_FILE_REDIRECTS:
                            raise UploadValidationError("Slack file download exceeded the redirect limit")
                        next_url = urljoin(str(resp.url), location)
                        if not _trusted_slack_download_url(next_url):
                            raise UploadValidationError("Slack file redirect left the trusted Slack host boundary")
                        current_url = next_url
                        continue

                    resp.raise_for_status()
                    with target_path.open("wb") as out:
                        async for chunk in resp.aiter_bytes():
                            written += len(chunk)
                            if written > limit:
                                raise UploadValidationError("File exceeds the configured upload-size limit")
                            out.write(chunk)
                    break
            else:  # pragma: no cover - defensive; loop exits through break/raise above
                raise UploadValidationError("Slack file download did not complete")

        _reject_html_masquerading_as_file(target_path)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise

    logger.info("Downloaded Slack file to %s (%s bytes)", target_path, written)
    return target_path


def extract_text(path: Path) -> str:
    """Extract plain text from a supported non-image local file."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UploadValidationError(f"Unsupported file type: {suffix}")
    if suffix in IMAGE_EXTENSIONS:
        raise UploadValidationError("Image text extraction requires the dedicated OCR adapter")
    if suffix == ".csv":
        return _extract_csv(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_csv(path: Path) -> str:
    lines = []
    with path.open(newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            lines.append(("Headers: " if i == 0 else "") + " | ".join(row))
            if i >= 500:
                lines.append("... (truncated)")
                break
    return "\n".join(lines)


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise UploadValidationError("PDF support requires the optional 'pypdf' package") from exc
    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()


def _extract_docx(path: Path) -> str:
    try:
        import docx
    except ImportError as exc:
        raise UploadValidationError("DOCX support requires the optional 'python-docx' package") from exc
    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs if p.text.strip())

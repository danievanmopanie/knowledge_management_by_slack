"""Download files from Slack and extract text for ingest."""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Any

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".log", ".json", ".yml", ".yaml"}


async def download_slack_file(file_info: dict[str, Any]) -> Path:
    """
    Download a Slack file to the local raw documents folder.

    `file_info` is the file object from a Slack event (contains url_private, name, etc.).
    """
    url = file_info.get("url_private") or file_info.get("url_private_download")
    if not url:
        raise ValueError("Slack file object has no downloadable URL")

    name = file_info.get("name") or file_info.get("id") or "unknown_file"
    # Sanitise filename
    safe_name = "".join(c for c in name if c.isalnum() or c in ("-", "_", ".", " ")).strip()
    if not safe_name:
        safe_name = file_info.get("id", "file")

    target_dir = settings.raw_docs_path
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name

    # Avoid overwriting – append id if needed
    if target_path.exists():
        stem = target_path.stem
        suffix = target_path.suffix
        target_path = target_dir / f"{stem}_{file_info.get('id', 'dup')}{suffix}"

    headers = {"Authorization": f"Bearer {settings.slack_bot_token}"}

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        target_path.write_bytes(resp.content)

    logger.info("Downloaded Slack file to %s (%s bytes)", target_path, target_path.stat().st_size)
    return target_path


def extract_text(path: Path) -> str:
    """
    Extract plain text from a local file.

    Currently supports common text formats. PDF/DOCX can be added later.
    """
    suffix = path.suffix.lower()

    if suffix not in SUPPORTED_TEXT_EXTENSIONS and suffix not in {".pdf", ".docx"}:
        # Try reading as text anyway for unknown extensions
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            raise ValueError(f"Unsupported file type: {suffix}")

    if suffix == ".csv":
        return _extract_csv(path)

    if suffix == ".pdf":
        return _extract_pdf(path)

    if suffix == ".docx":
        return _extract_docx(path)

    # Default: plain text / markdown / json / yaml / log
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_csv(path: Path) -> str:
    """Convert CSV into a readable text representation for embedding."""
    lines = []
    with path.open(newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                lines.append("Headers: " + " | ".join(row))
            else:
                lines.append(" | ".join(row))
            if i >= 500:  # safety limit for very large CSVs
                lines.append("... (truncated)")
                break
    return "\n".join(lines)


def _extract_pdf(path: Path) -> str:
    """Extract text from PDF if pypdf is available."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError(
            "PDF support requires the 'pypdf' package. "
            "Install it or convert the file to Markdown/TXT."
        )

    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)


def _extract_docx(path: Path) -> str:
    """Extract text from DOCX if python-docx is available."""
    try:
        import docx
    except ImportError:
        raise ValueError(
            "DOCX support requires the 'python-docx' package. "
            "Install it or convert the file to Markdown/TXT."
        )

    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs if p.text.strip())

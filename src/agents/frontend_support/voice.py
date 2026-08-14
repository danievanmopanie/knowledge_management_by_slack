"""Local transcription of Slack voice notes for Frontend Support."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".webm", ".mp4", ".aac"}
_MODEL = None


class VoiceTranscriptionError(RuntimeError):
    """Raised when a voice note cannot be downloaded or transcribed."""


def is_audio_file(file_info: dict[str, Any]) -> bool:
    mimetype = str(file_info.get("mimetype") or "").lower()
    filetype = str(file_info.get("filetype") or "").lower()
    suffix = Path(str(file_info.get("name") or "")).suffix.lower()
    return mimetype.startswith("audio/") or filetype in {
        "m4a",
        "mp3",
        "wav",
        "ogg",
        "webm",
        "aac",
    } or suffix in AUDIO_EXTENSIONS


def audio_files(files: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [file_info for file_info in (files or []) if is_audio_file(file_info)]


async def _download_audio(file_info: dict[str, Any]) -> Path:
    url = file_info.get("url_private_download") or file_info.get("url_private")
    if not url:
        raise VoiceTranscriptionError("Slack voice note has no downloadable URL")

    size = file_info.get("size")
    if size is not None and int(size) > settings.max_upload_bytes:
        raise VoiceTranscriptionError("Voice note exceeds the configured upload-size limit")

    file_id = str(file_info.get("id") or "voice")
    suffix = Path(str(file_info.get("name") or "voice.m4a")).suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        suffix = ".m4a"
    target_dir = settings.staging_path / "voice"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / f"{file_id}{suffix}").resolve()
    if target_dir.resolve() not in target.parents:
        raise VoiceTranscriptionError("Unsafe voice-note download path")

    headers = {"Authorization": f"Bearer {settings.slack_bot_token}"}
    written = 0
    try:
        async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
            async with client.stream("GET", str(url), headers=headers) as response:
                response.raise_for_status()
                with target.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        written += len(chunk)
                        if written > settings.max_upload_bytes:
                            raise VoiceTranscriptionError(
                                "Voice note exceeds the configured upload-size limit"
                            )
                        handle.write(chunk)
    except Exception as exc:
        target.unlink(missing_ok=True)
        if isinstance(exc, VoiceTranscriptionError):
            raise
        raise VoiceTranscriptionError(f"Could not download Slack voice note: {exc}") from exc

    return target


def _get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VoiceTranscriptionError(
            "Voice transcription requires the optional dependency: pip install -e '.[voice]'"
        ) from exc

    _MODEL = WhisperModel(
        settings.voice_transcription_model,
        device=settings.voice_transcription_device,
        compute_type=settings.voice_transcription_compute_type,
    )
    return _MODEL


def _transcribe_sync(path: Path) -> str:
    model = _get_model()
    segments, _info = model.transcribe(str(path), vad_filter=True, beam_size=3)
    text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    if not text:
        raise VoiceTranscriptionError("No speech could be extracted from the voice note")
    return text


async def transcribe_voice_note(file_info: dict[str, Any]) -> str:
    """Download and locally transcribe one Slack voice note."""
    if not settings.voice_notes_enabled:
        raise VoiceTranscriptionError("Voice-note transcription is disabled")
    path = await _download_audio(file_info)
    try:
        return await asyncio.to_thread(_transcribe_sync, path)
    finally:
        path.unlink(missing_ok=True)


async def transcribe_first_voice_note(files: list[dict[str, Any]] | None) -> str | None:
    matches = audio_files(files)
    if not matches:
        return None
    transcript = await transcribe_voice_note(matches[0])
    logger.info("Transcribed Slack voice note file_id=%s", matches[0].get("id"))
    return transcript

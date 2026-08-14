"""Pure unit tests for Slack voice-note detection."""

from src.agents.frontend_support.voice import audio_files, is_audio_file


def test_detects_slack_voice_note_from_mimetype():
    file_info = {
        "id": "F1",
        "name": "voice-note.bin",
        "mimetype": "audio/mp4",
    }
    assert is_audio_file(file_info) is True


def test_detects_common_audio_extension_without_mimetype():
    assert is_audio_file({"id": "F2", "name": "incident-note.m4a"}) is True


def test_non_audio_attachments_are_not_sent_to_transcriber():
    files = [
        {"id": "F1", "name": "screenshot.png", "mimetype": "image/png"},
        {"id": "F2", "name": "incident.m4a", "mimetype": "audio/mp4"},
        {"id": "F3", "name": "notes.txt", "mimetype": "text/plain"},
    ]
    assert [item["id"] for item in audio_files(files)] == ["F2"]

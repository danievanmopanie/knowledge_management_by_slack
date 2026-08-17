import json

from src.agents.frontend_support import trigger_feedback


def test_explicit_mention_feedback_is_appended_as_jsonl(tmp_path, monkeypatch):
    log_path = tmp_path / "missed_triggers.jsonl"
    monkeypatch.setattr(trigger_feedback, "_log_path", lambda: log_path)

    result = trigger_feedback.append_trigger_feedback(
        channel_id="C-FRONT",
        thread_ts="100.1",
        mention_ts="100.2",
        user_id="U1",
        mention_text="help here",
        root_text="User's screen shows BSOD",
        thread_events=[{"user_id": "U1", "text": "User's screen shows BSOD"}],
        root_already_looked_like_support=False,
    )

    assert result == log_path
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["root_text"] == "User's screen shows BSOD"
    assert records[0]["mention_text"] == "help here"
    assert records[0]["root_already_looked_like_support"] is False

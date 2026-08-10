"""Tests for the Slack report publisher's thread_ts forwarding."""

from src.core.config import settings
from src.reporting import publisher


class FakeWebClient:
    def __init__(self, token):
        self.token = token

    def chat_postMessage(self, **kwargs):
        FakeWebClient.last_call = kwargs
        return {"ts": "111.222"}


def test_publish_report_forwards_thread_ts(monkeypatch):
    monkeypatch.setattr(publisher, "WebClient", FakeWebClient)
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-test")

    result = publisher.publish_report_to_channel("hello", channel_id="C1", thread_ts="123.45")

    assert FakeWebClient.last_call["thread_ts"] == "123.45"
    assert result == {"ok": True, "channel": "C1", "ts": "111.222"}


def test_publish_report_without_thread_ts_passes_none(monkeypatch):
    monkeypatch.setattr(publisher, "WebClient", FakeWebClient)
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-test")

    publisher.publish_report_to_channel("hello", channel_id="C1")

    assert FakeWebClient.last_call["thread_ts"] is None

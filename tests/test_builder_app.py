from __future__ import annotations

from src.bot import builder_app


def test_clean_text_accepts_natural_language_and_mentions():
    assert builder_app.clean_text("<@U123ABC> Check my PRs, please") == "Check my PRs, please"
    assert builder_app.clean_text("Check my PRs, please") == "Check my PRs, please"


def test_human_builder_user_is_trusted(monkeypatch):
    monkeypatch.setattr(builder_app.settings, "builder_agent_allowed_user_ids", "U_DANIE,U_OTHER")
    monkeypatch.setattr(builder_app.settings, "builder_trusted_slack_sender_ids", "")
    assert builder_app.is_trusted_event({"user": "U_DANIE"}) is True
    assert builder_app.is_trusted_event({"user": "U_RANDOM"}) is False


def test_only_explicit_external_bot_or_app_is_trusted(monkeypatch):
    monkeypatch.setattr(builder_app.settings, "builder_agent_allowed_user_ids", "U_DANIE")
    monkeypatch.setattr(builder_app.settings, "builder_trusted_slack_sender_ids", "B_CHATGPT,A_CHATGPT")
    assert builder_app.is_trusted_event({"subtype": "bot_message", "bot_id": "B_CHATGPT"}) is True
    assert builder_app.is_trusted_event({"subtype": "bot_message", "app_id": "A_CHATGPT"}) is True
    assert builder_app.is_trusted_event({"subtype": "bot_message", "bot_id": "B_UNKNOWN"}) is False


def test_slack_redelivery_is_idempotent():
    builder_app._seen.clear()
    builder_app._seen_order.clear()
    event = {"channel": "C_BUILDER", "ts": "123.456", "client_msg_id": "same-message"}
    assert builder_app.mark_first_delivery(event) is True
    assert builder_app.mark_first_delivery(event) is False


def test_same_text_in_different_messages_is_not_deduplicated():
    builder_app._seen.clear()
    builder_app._seen_order.clear()
    first = {"channel": "C_BUILDER", "ts": "123.456"}
    second = {"channel": "C_BUILDER", "ts": "123.457"}
    assert builder_app.mark_first_delivery(first) is True
    assert builder_app.mark_first_delivery(second) is True


def test_event_sender_prefers_human_user_for_assisted_messages():
    event = {"user": "U_DANIE", "app_id": "A_CHATGPT"}
    assert builder_app.event_sender_id(event) == "U_DANIE"

from src.bot.blockkit.builder import builder_status_blocks
from src.reporting import publisher
from src.reporting.publisher import normalize_slack_mrkdwn


def test_read_only_answer_card_hides_internal_branch_and_turn():
    blocks = builder_status_blocks(
        task_id="bld_123",
        status="answered",
        summary="I checked the repository.",
        branch_name="builder/bld_123",
    )

    rendered = str(blocks)
    assert "builder/bld_123" not in rendered
    assert "bld_123" not in rendered
    assert "Builder runs on-device" not in rendered
    assert "Done" in rendered


def test_slack_mrkdwn_unescapes_model_output_and_converts_heading():
    raw = r"\### Open PRs\n\*\*3 PRs\*\* are ready"

    cleaned = normalize_slack_mrkdwn(raw)

    assert "\\###" not in cleaned
    assert "\\*" not in cleaned
    assert "*Open PRs*" in cleaned
    assert "*3 PRs*" in cleaned
    assert "**3 PRs**" not in cleaned


def test_slack_mrkdwn_unescapes_list_markers():
    cleaned = normalize_slack_mrkdwn(r"\- first item\n\- second item")

    assert "\\-" not in cleaned
    assert cleaned == "- first item\n- second item"


def test_slack_mrkdwn_converts_markdown_table_to_bullets():
    raw = """| # | Title | State |
|---|---|---|
| 79 | Builder ingress | Open |
| 76 | Knowledge flow | Open |"""

    cleaned = normalize_slack_mrkdwn(raw)

    assert "|---|" not in cleaned
    assert "• *#:* 79" in cleaned
    assert "*Title:* Builder ingress" in cleaned
    assert "• *#:* 76" in cleaned


def test_builder_channel_publishing_uses_dedicated_builder_bot_token(monkeypatch):
    captured: dict[str, str] = {}

    class FakeWebClient:
        def __init__(self, *, token: str):
            captured["token"] = token

        def chat_postMessage(self, **kwargs):
            return {"ts": "123.456"}

    monkeypatch.setattr(publisher.settings, "channel_builder_agent", "C_BUILDER")
    monkeypatch.setattr(publisher.settings, "builder_slack_bot_token", "xoxb-builder")
    monkeypatch.setattr(publisher.settings, "slack_bot_token", "xoxb-generic")
    monkeypatch.setattr(publisher, "WebClient", FakeWebClient)

    publisher.publish_report_to_channel("Working", channel_id="C_BUILDER")

    assert captured["token"] == "xoxb-builder"


def test_non_builder_channel_publishing_keeps_generic_bot_token(monkeypatch):
    captured: dict[str, str] = {}

    class FakeWebClient:
        def __init__(self, *, token: str):
            captured["token"] = token

        def chat_postMessage(self, **kwargs):
            return {"ts": "123.456"}

    monkeypatch.setattr(publisher.settings, "channel_builder_agent", "C_BUILDER")
    monkeypatch.setattr(publisher.settings, "builder_slack_bot_token", "xoxb-builder")
    monkeypatch.setattr(publisher.settings, "slack_bot_token", "xoxb-generic")
    monkeypatch.setattr(publisher, "WebClient", FakeWebClient)

    publisher.publish_report_to_channel("Working", channel_id="C_FRONTEND")

    assert captured["token"] == "xoxb-generic"

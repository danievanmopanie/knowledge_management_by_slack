from types import SimpleNamespace

from src.bot.readiness import validate_slack_readiness


def settings(**overrides):
    values = dict(
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        slack_signing_secret="secret",
        channel_inventory="CINV",
        channel_frontend_support="CFRONT",
        channel_work_management="CWORK",
        channel_knowledge_uploads="CKNOW",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_inventory_pilot_configuration_is_ready():
    result = validate_slack_readiness(settings())
    assert result.ready is True
    assert result.errors == ()


def test_inventory_channel_is_required():
    result = validate_slack_readiness(settings(channel_inventory=None))
    assert result.ready is False
    assert any("CHANNEL_INVENTORY" in item for item in result.errors)


def test_duplicate_agent_channels_are_rejected():
    result = validate_slack_readiness(settings(channel_inventory="CSAME", channel_frontend_support="CSAME"))
    assert result.ready is False
    assert any("multiple agents" in item for item in result.errors)


def test_unconfigured_non_inventory_channels_are_warnings():
    result = validate_slack_readiness(
        settings(
            channel_frontend_support=None,
            channel_work_management=None,
            channel_knowledge_uploads=None,
        )
    )
    assert result.ready is True
    assert len(result.warnings) == 3


def test_placeholder_tokens_are_rejected():
    result = validate_slack_readiness(settings(slack_bot_token="xoxb-your-bot-token"))
    assert result.ready is False
    assert any("SLACK_BOT_TOKEN" in item for item in result.errors)

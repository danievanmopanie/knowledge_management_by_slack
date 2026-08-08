"""Routing tests for typed request context and configured agent channels."""

import asyncio

from src.bot import router
from src.core.context import RequestContext


class FakeAgent:
    name = "fake"

    async def handle(self, message: str, context: RequestContext) -> str:
        return f"{context.request_id}:{message}"


def test_route_message_passes_typed_context(monkeypatch):
    monkeypatch.setattr(router, "_channel_to_agent", lambda _channel: FakeAgent())
    context = RequestContext.from_slack(
        channel_id="C123",
        user_id="U123",
        request_id="req123",
    )

    result = asyncio.run(router.route_message("hello", context))

    assert result == "req123:hello"


def test_inventory_channel_maps_to_inventory_agent(monkeypatch):
    monkeypatch.setattr(router.settings, "channel_inventory", "CINV")
    monkeypatch.setattr(router.settings, "channel_frontend_support", "CFRONT")
    monkeypatch.setattr(router.settings, "channel_work_management", "CWORK")
    monkeypatch.setattr(router.settings, "channel_knowledge_uploads", "CKNOW")

    assert router._channel_to_agent("CINV") is router._inventory


def test_unconfigured_channel_returns_setup_guidance(monkeypatch):
    monkeypatch.setattr(router.settings, "channel_inventory", "CINV")
    monkeypatch.setattr(router.settings, "channel_frontend_support", None)
    monkeypatch.setattr(router.settings, "channel_work_management", None)
    monkeypatch.setattr(router.settings, "channel_knowledge_uploads", None)
    context = RequestContext.from_slack(channel_id="COTHER", user_id="U123")

    result = asyncio.run(router.route_message("hello", context))

    assert "not yet configured" in result
    assert "CHANNEL_INVENTORY" in result

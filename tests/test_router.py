"""Routing tests for typed request context."""

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

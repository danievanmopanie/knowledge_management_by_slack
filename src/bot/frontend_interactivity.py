"""Slack interactivity for frontend-support knowledge capture."""

from __future__ import annotations

import json
import logging

from slack_bolt.async_app import AsyncApp

from src.agents.frontend_support.collaboration import FrontendCollaborationService
from src.bot.frontend_actions import CAPTURE_RESOLUTION, DISMISS_RESOLUTION

logger = logging.getLogger(__name__)

_service: FrontendCollaborationService | None = None


def get_service() -> FrontendCollaborationService:
    global _service
    if _service is None:
        _service = FrontendCollaborationService()
    return _service


def _remove_capture_controls(blocks: list[dict]) -> list[dict]:
    return [
        block
        for block in blocks
        if block.get("block_id") != "frontend_resolution_capture"
    ]


def register(app: AsyncApp) -> None:
    @app.action(CAPTURE_RESOLUTION)
    async def capture_resolution(ack, body, client):
        await ack()
        action = body["actions"][0]
        payload = json.loads(action["value"])
        channel_id = payload["channel_id"]
        thread_ts = payload["thread_ts"]
        actor_id = body["user"]["id"]
        result = get_service().confirm_knowledge(channel_id, thread_ts, actor_id)

        message = body.get("message") or {}
        message_ts = message.get("ts")
        if message_ts and result.startswith("Captured this resolution"):
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=message.get("text", "Knowledge capture confirmed."),
                blocks=_remove_capture_controls(message.get("blocks", [])),
            )
        await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=result)

    @app.action(DISMISS_RESOLUTION)
    async def dismiss_resolution(ack, body, client):
        await ack()
        message = body.get("message") or {}
        channel_id = body.get("channel", {}).get("id")
        message_ts = message.get("ts")
        if channel_id and message_ts:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=message.get("text", "Knowledge capture dismissed."),
                blocks=_remove_capture_controls(message.get("blocks", [])),
            )

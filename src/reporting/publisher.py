"""Publish generated reports and durable worker cards into Slack."""

from __future__ import annotations

import logging
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.core.config import settings

logger = logging.getLogger(__name__)


def publish_report_to_channel(
    text: str,
    channel_id: str | None = None,
    thread_ts: str | None = None,
    *,
    blocks: list[dict[str, Any]] | None = None,
    update_message_ts: str | None = None,
) -> dict[str, Any]:
    """Post or update a Slack message.

    `text` is always supplied as the accessible notification/fallback string.
    When `update_message_ts` is set the existing message is updated in place;
    this is used by Builder progress cards to avoid status-message spam.
    """
    channel = channel_id or settings.channel_frontend_support
    if not channel:
        raise ValueError(
            "No channel configured. Set CHANNEL_FRONTEND_SUPPORT in .env "
            "or pass channel_id explicitly."
        )

    client = WebClient(token=settings.slack_bot_token)
    if len(text) > 35000:
        text = text[:34900] + "\n\n_…report truncated_"

    try:
        if update_message_ts:
            result = client.chat_update(
                channel=channel,
                ts=update_message_ts,
                text=text,
                blocks=blocks,
            )
            message_ts = result.get("ts") or update_message_ts
            logger.info("Updated Slack message in %s (ts=%s)", channel, message_ts)
        else:
            result = client.chat_postMessage(
                channel=channel,
                text=text,
                mrkdwn=True,
                thread_ts=thread_ts,
                blocks=blocks,
            )
            message_ts = result.get("ts")
            logger.info("Published report to %s (ts=%s)", channel, message_ts)
        return {"ok": True, "channel": channel, "ts": message_ts}
    except SlackApiError as e:
        logger.error("Slack API error publishing report: %s", e.response.get("error"))
        raise

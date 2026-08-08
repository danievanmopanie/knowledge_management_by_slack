"""Publish generated reports into a Slack channel."""

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
) -> dict[str, Any]:
    """
    Post a report message to the Frontend Support channel (or override).

    Slack has a practical limit around 40k characters per message; we keep
    reports well under that. Long reports can later be split or uploaded as files.
    """
    channel = channel_id or settings.channel_frontend_support
    if not channel:
        raise ValueError(
            "No channel configured. Set CHANNEL_FRONTEND_SUPPORT in .env "
            "or pass channel_id explicitly."
        )

    client = WebClient(token=settings.slack_bot_token)

    # Soft trim to stay safe in Slack
    if len(text) > 35000:
        text = text[:34900] + "\n\n_…report truncated_"

    try:
        result = client.chat_postMessage(
            channel=channel,
            text=text,
            mrkdwn=True,
        )
        logger.info("Published report to %s (ts=%s)", channel, result.get("ts"))
        return {"ok": True, "channel": channel, "ts": result.get("ts")}
    except SlackApiError as e:
        logger.error("Slack API error publishing report: %s", e.response.get("error"))
        raise

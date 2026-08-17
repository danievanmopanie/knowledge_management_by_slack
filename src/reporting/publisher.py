"""Publish generated reports and durable worker cards into Slack."""

from __future__ import annotations

import logging
import re
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.core.config import settings

logger = logging.getLogger(__name__)
_MARKDOWN_HEADING_RE = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")
_ESCAPED_MRKDWN_RE = re.compile(r"\\([*_#|`-])")
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _convert_markdown_tables(text: str) -> str:
    """Convert simple Markdown tables into compact Slack-native bullet lists."""
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        if (
            index + 1 < len(lines)
            and "|" in lines[index]
            and _TABLE_SEPARATOR_RE.match(lines[index + 1])
        ):
            headers = _table_cells(lines[index])
            index += 2
            rows: list[str] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                values = _table_cells(lines[index])
                parts = [
                    f"*{header}:* {value}"
                    for header, value in zip(headers, values)
                    if header and value
                ]
                if parts:
                    rows.append("• " + " · ".join(parts))
                index += 1
            output.extend(rows)
            continue
        output.append(lines[index])
        index += 1
    return "\n".join(output)


def normalize_slack_mrkdwn(text: str) -> str:
    """Make model-authored Markdown readable in Slack mrkdwn."""
    cleaned = (text or "").replace("\\n", "\n")
    cleaned = _ESCAPED_MRKDWN_RE.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_HEADING_RE.sub(r"*\1*", cleaned)
    cleaned = _MARKDOWN_BOLD_RE.sub(r"*\1*", cleaned)
    cleaned = _convert_markdown_tables(cleaned)
    return cleaned


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
    text = normalize_slack_mrkdwn(text)
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

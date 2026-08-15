"""Conversation helpers for natural Frontend Support Slack interactions."""

from __future__ import annotations

import re
from typing import Protocol

# Broad field-support vocabulary used only as a safety net when the cheap
# collaboration classifier does not recognise a technical problem. The
# collaboration service remains the source of truth for thread state.
SUPPORT_TERMS = (
    "bluetooth",
    "headset",
    "headphones",
    "earphones",
    "mouse",
    "keyboard",
    "monitor",
    "dock",
    "docking",
    "camera",
    "webcam",
    "microphone",
    "speaker",
    "connect",
    "connecting",
    "connection",
    "disconnect",
    "disconnected",
    "pair",
    "pairing",
    "paired",
    "not working",
    "won't",
    "isn't",
    "cannot",
    "can't",
    "unable",
    "error",
    "issue",
    "problem",
    "fails",
    "failed",
    "failing",
)

MENTION_RE = re.compile(r"<@[A-Z0-9]+>\s*", re.I)


class ThreadQueryBuilder(Protocol):
    def build_agent_query(self, channel_id: str, thread_ts: str) -> str: ...


def clean_mention_text(text: str) -> str:
    """Remove Slack bot mentions while preserving the technician's words."""
    return MENTION_RE.sub("", text or "").strip(" -,:\t\n")


def looks_like_support(text: str) -> bool:
    """Return True for common natural-language field-support signals."""
    lowered = (text or "").lower()
    return any(term in lowered for term in SUPPORT_TERMS)


def compose_thread_query(
    service: ThreadQueryBuilder,
    *,
    channel_id: str,
    thread_ts: str,
    latest_text: str = "",
) -> str:
    """Resolve terse/anaphoric requests against the stored collaborative thread.

    A message such as "help with this" is meaningless in isolation. The agent
    must reason over the root issue and all stored technician contributions.
    """
    try:
        query = service.build_agent_query(channel_id, thread_ts)
    except KeyError:
        return latest_text.strip()

    latest = latest_text.strip()
    if latest and latest.lower() not in query.lower():
        query = f"{query}\n\nLatest explicit request: {latest}"
    return query

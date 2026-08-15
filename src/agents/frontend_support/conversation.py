"""Conversation helpers for natural Frontend Support Slack interactions."""

from __future__ import annotations

import re
from typing import Protocol

# Broad field-support vocabulary used only as a safety net when the cheap
# collaboration classifier does not recognise a technical problem. The
# collaboration service remains the source of truth for thread state.
SUPPORT_TERMS = (
    # Common device/peripheral language.
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
    # High-signal endpoint failure language technicians often use tersely.
    "bsod",
    "blue screen",
    "blue-screen",
    "stop code",
    "crashed",
    "crash",
    "frozen",
    "freeze",
    "hung",
    "black screen",
    "blank screen",
    "no display",
    "won't boot",
    "not booting",
    "boot loop",
    "reboot loop",
    "keeps restarting",
    "won't start",
    "not starting",
    "won't open",
    "not opening",
    "won't launch",
    "not launching",
    "not responding",
    # Connectivity and generic failure language.
    "timeout",
    "timed out",
    "timing out",
    "times out",
    "connect",
    "connecting",
    "connection",
    "disconnect",
    "disconnected",
    "pair",
    "pairing",
    "paired",
    "offline",
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
INCIDENT_RE = re.compile(r"\bINC\d{4,}\b", re.I)


class ThreadQueryBuilder(Protocol):
    def build_agent_query(self, channel_id: str, thread_ts: str) -> str: ...


def clean_mention_text(text: str) -> str:
    """Remove Slack bot mentions while preserving the technician's words."""
    return MENTION_RE.sub("", text or "").strip(" -,:\t\n")


def looks_like_support(text: str) -> bool:
    """Return True for natural-language field-support signals or incident lookups.

    This deliberately favors recall over precision inside #frontend-support:
    false positives are less costly than forcing technicians to learn trigger
    words or @mention the bot for obvious support problems. A named ServiceNow
    INC reference is itself a high-signal support request and should wake the
    agent even when the surrounding sentence contains no failure vocabulary.
    """
    raw = text or ""
    if INCIDENT_RE.search(raw):
        return True
    lowered = raw.lower()
    return any(term in lowered for term in SUPPORT_TERMS)


def compose_thread_query(
    service: ThreadQueryBuilder,
    *,
    channel_id: str,
    thread_ts: str,
    latest_text: str = "",
) -> str:
    """Resolve terse/anaphoric requests against the stored collaborative thread."""
    try:
        query = service.build_agent_query(channel_id, thread_ts)
    except KeyError:
        return latest_text.strip()

    latest = latest_text.strip()
    if latest and latest.lower() not in query.lower():
        query = f"{query}\n\nLatest explicit request: {latest}"
    return query

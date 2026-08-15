"""Feedback loop for missed proactive Frontend Support triggers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.config import settings

DEFAULT_LOG_PATH = Path("./data/frontend_support/missed_triggers.jsonl")


def _log_path() -> Path:
    return Path(getattr(settings, "frontend_trigger_feedback_log_path", DEFAULT_LOG_PATH))


def append_trigger_feedback(
    *,
    channel_id: str,
    thread_ts: str,
    mention_ts: str,
    user_id: str,
    mention_text: str,
    root_text: str,
    thread_events: list[dict[str, Any]],
    root_already_looked_like_support: bool,
) -> Path:
    """Append one explicit @mention as training evidence for trigger maintenance.

    We intentionally log every explicit Frontend Support mention in the support
    channel. The two-day review job decides whether the root wording represents
    a genuine trigger miss or simply a deliberate request for the agent.
    """
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "mention_ts": mention_ts,
        "user_id": user_id,
        "mention_text": mention_text,
        "root_text": root_text,
        "root_already_looked_like_support": root_already_looked_like_support,
        "thread_events": thread_events,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path

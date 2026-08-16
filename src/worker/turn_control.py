"""Cooperative stop checkpoint shared by the terminal harness and repair loop.

The Bolt app and the Builder worker are separate OS processes, so a Slack
Cancel click cannot reach into an in-flight subprocess directly. Instead it
sets a flag in SQLite; the worker polls it between steps via `TurnControl`.
This is cooperative, not preemptive: it stops the *next* step from starting,
it cannot interrupt a step already running.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.agents.builder.task_store import BuilderTaskStore


@dataclass
class TurnControl:
    store: BuilderTaskStore
    task_id: str
    deadline_at: datetime | None

    def check(self) -> str | None:
        """Return 'cancelled', 'deadline', or None. One cheap indexed read."""
        if self.deadline_at and datetime.now(timezone.utc) >= self.deadline_at:
            return "deadline"
        row = self.store.get(self.task_id)
        if row and row.get("cancel_requested"):
            return "cancelled"
        return None


def parse_deadline(deadline_at: str | None) -> datetime | None:
    if not deadline_at:
        return None
    try:
        return datetime.fromisoformat(deadline_at)
    except ValueError:
        return None

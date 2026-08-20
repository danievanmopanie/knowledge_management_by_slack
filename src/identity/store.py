"""Durable Slack → external-system identity mapping on the platform SQLite DB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.core.database import connect


@dataclass(frozen=True)
class IdentityRecord:
    """A single Slack user's known external identities."""

    slack_user_id: str
    email: str | None = None
    display_name: str | None = None
    snipeit_user_id: str | None = None
    taskwondo_user_id: str | None = None


class IdentityStore:
    """Persists Slack ↔ Snipe-IT ↔ Taskwondo user mappings."""

    def __init__(self, path: Path | None = None):
        self.path = path
        with connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS identity_map (
                    slack_user_id TEXT PRIMARY KEY,
                    email TEXT,
                    display_name TEXT,
                    snipeit_user_id TEXT,
                    taskwondo_user_id TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_identity_email ON identity_map(email)")

    def get(self, slack_user_id: str) -> IdentityRecord | None:
        if not slack_user_id:
            return None
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM identity_map WHERE slack_user_id=?", (slack_user_id,)
            ).fetchone()
        if row is None:
            return None
        return IdentityRecord(
            slack_user_id=row["slack_user_id"],
            email=row["email"],
            display_name=row["display_name"],
            snipeit_user_id=row["snipeit_user_id"],
            taskwondo_user_id=row["taskwondo_user_id"],
        )

    def upsert(
        self,
        slack_user_id: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
        snipeit_user_id: str | None = None,
        taskwondo_user_id: str | None = None,
    ) -> IdentityRecord:
        """Insert or update a mapping. Only non-None fields overwrite existing values."""
        if not slack_user_id:
            raise ValueError("slack_user_id is required to store an identity mapping.")
        existing = self.get(slack_user_id)
        merged = IdentityRecord(
            slack_user_id=slack_user_id,
            email=email if email is not None else (existing.email if existing else None),
            display_name=(
                display_name if display_name is not None else (existing.display_name if existing else None)
            ),
            snipeit_user_id=(
                str(snipeit_user_id)
                if snipeit_user_id is not None
                else (existing.snipeit_user_id if existing else None)
            ),
            taskwondo_user_id=(
                str(taskwondo_user_id)
                if taskwondo_user_id is not None
                else (existing.taskwondo_user_id if existing else None)
            ),
        )
        with connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO identity_map
                    (slack_user_id, email, display_name, snipeit_user_id, taskwondo_user_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(slack_user_id) DO UPDATE SET
                    email=excluded.email,
                    display_name=excluded.display_name,
                    snipeit_user_id=excluded.snipeit_user_id,
                    taskwondo_user_id=excluded.taskwondo_user_id,
                    updated_at=excluded.updated_at
                """,
                (
                    merged.slack_user_id,
                    merged.email,
                    merged.display_name,
                    merged.snipeit_user_id,
                    merged.taskwondo_user_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return merged

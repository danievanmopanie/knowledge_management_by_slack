"""Durable staging records for confirmation-before-ingest workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.core.database import connect


class StagingStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        with connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS staged_uploads (
                    stage_id TEXT PRIMARY KEY,
                    slack_file_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    uploader_id TEXT,
                    channel_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def create(
        self,
        *,
        slack_file_id: str,
        file_name: str,
        local_path: str,
        uploader_id: str | None,
        channel_id: str,
    ) -> str:
        stage_id = "stg_" + uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                """INSERT INTO staged_uploads
                (stage_id, slack_file_id, file_name, local_path, uploader_id, channel_id,
                 status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'staged', ?, ?)""",
                (
                    stage_id,
                    slack_file_id,
                    file_name,
                    local_path,
                    uploader_id,
                    channel_id,
                    now,
                    now,
                ),
            )
        return stage_id

    def get(self, stage_id: str) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM staged_uploads WHERE stage_id=?", (stage_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_staged(self, *, uploader_id: str | None, channel_id: str) -> list[dict[str, Any]]:
        """Return still-pending uploads for one uploader in one channel, oldest first.

        Used by bulk "confirm all" so onboarding many existing knowledge articles at
        once doesn't require confirming each staged file individually.
        """
        with connect(self.path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM staged_uploads
                WHERE status='staged' AND channel_id=? AND uploader_id IS ?
                ORDER BY created_at
                """,
                (channel_id, uploader_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_status(self, stage_id: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                "UPDATE staged_uploads SET status=?, updated_at=? WHERE stage_id=?",
                (status, now, stage_id),
            )

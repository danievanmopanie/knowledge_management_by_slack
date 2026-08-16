"""Audit trail for Builder Merge & Deploy actions.

Merge & Deploy is a privileged, human-triggered operation (merges a PR,
restarts a live service via a narrowly-scoped sudoers rule) — who clicked it
and what happened deserves its own durable record, separate from the ordinary
coding-turn queue in task_store.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.core.database import connect


class BuilderDeploymentStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        with connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS builder_deployments (
                    deployment_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    pr_number TEXT NOT NULL,
                    pr_url TEXT NOT NULL,
                    triggered_by TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_ts TEXT,
                    status TEXT NOT NULL,
                    merge_sha TEXT,
                    restarted_units TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_builder_deployments_pr "
                "ON builder_deployments(pr_number, created_at)"
            )

    def record(
        self,
        *,
        task_id: str | None,
        pr_number: str,
        pr_url: str,
        triggered_by: str,
        channel_id: str,
        message_ts: str | None,
    ) -> str:
        deployment_id = "dep_" + uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                """INSERT INTO builder_deployments
                (deployment_id, task_id, pr_number, pr_url, triggered_by, channel_id,
                 message_ts, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'started', ?)""",
                (deployment_id, task_id, pr_number, pr_url, triggered_by, channel_id, message_ts, now),
            )
        return deployment_id

    def mark_result(
        self,
        deployment_id: str,
        *,
        success: bool,
        merge_sha: str | None,
        restarted: list[tuple[str, bool, str]],
        error_message: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                """UPDATE builder_deployments
                SET status=?, merge_sha=?, restarted_units=?, error_message=?, finished_at=?
                WHERE deployment_id=?""",
                (
                    "succeeded" if success else "failed",
                    merge_sha,
                    json.dumps(restarted),
                    error_message,
                    now,
                    deployment_id,
                ),
            )

    def get(self, deployment_id: str) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM builder_deployments WHERE deployment_id=?", (deployment_id,)
            ).fetchone()
        return dict(row) if row else None

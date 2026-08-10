"""Durable task queue for Builder Agent (Aider-driven) coding tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.core.database import connect


class BuilderTaskStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        with connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS builder_tasks (
                    task_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    requester_id TEXT,
                    channel_id TEXT NOT NULL,
                    thread_ts TEXT,
                    status TEXT NOT NULL,
                    branch_name TEXT,
                    pr_url TEXT,
                    error_message TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_builder_tasks_status ON builder_tasks(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_builder_tasks_requester "
                "ON builder_tasks(requester_id)"
            )

    def enqueue(
        self,
        *,
        goal: str,
        requester_id: str | None,
        channel_id: str,
        thread_ts: str | None,
    ) -> str:
        task_id = "bld_" + uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                """INSERT INTO builder_tasks
                (task_id, goal, requester_id, channel_id, thread_ts, status,
                 attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)""",
                (task_id, goal, requester_id, channel_id, thread_ts, now, now),
            )
        return task_id

    def get(self, task_id: str) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM builder_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_by_requester(self, requester_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with connect(self.path) as conn:
            rows = conn.execute(
                "SELECT * FROM builder_tasks WHERE requester_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (requester_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically claim the oldest pending task, flipping it to 'running'.

        Single-worker design: BEGIN IMMEDIATE serializes this against concurrent
        enqueue() writers, and the UPDATE...WHERE(subquery)...RETURNING is one
        atomic statement so there is no separate select-then-update race window.
        Running two worker processes against this store would need an added
        lease/heartbeat column; not needed for the current single-worker deploy.
        """
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    UPDATE builder_tasks
                    SET status='running', started_at=?, updated_at=?, attempts=attempts + 1
                    WHERE task_id = (
                        SELECT task_id FROM builder_tasks
                        WHERE status='pending'
                        ORDER BY created_at
                        LIMIT 1
                    )
                    RETURNING *
                    """,
                    (now, now),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return dict(row) if row else None

    def mark_running(self, task_id: str, *, branch_name: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                "UPDATE builder_tasks SET branch_name=?, updated_at=? WHERE task_id=?",
                (branch_name, now, task_id),
            )

    def mark_succeeded(self, task_id: str, *, pr_url: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                """UPDATE builder_tasks
                SET status='succeeded', pr_url=?, updated_at=?, finished_at=?
                WHERE task_id=?""",
                (pr_url, now, now, task_id),
            )

    def mark_failed(self, task_id: str, *, error_message: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                """UPDATE builder_tasks
                SET status='failed', error_message=?, updated_at=?, finished_at=?
                WHERE task_id=?""",
                (error_message, now, now, task_id),
            )

    def mark_cancelled(self, task_id: str) -> bool:
        """Cancel a task while it is still pending. Returns False if it was not pending."""
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            cursor = conn.execute(
                "UPDATE builder_tasks SET status='cancelled', updated_at=?, finished_at=? "
                "WHERE task_id=? AND status='pending'",
                (now, now, task_id),
            )
        return cursor.rowcount > 0

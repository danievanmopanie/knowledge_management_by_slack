"""Durable task queue for Builder Agent coding-harness turns."""

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
                    handoff_pr_number TEXT,
                    continuation_branch TEXT,
                    continuation_pr_url TEXT,
                    progress_message_ts TEXT,
                    result_text TEXT,
                    error_message TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            # Safe additive migration for existing Builder databases.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(builder_tasks)").fetchall()}
            for name in (
                "progress_message_ts",
                "result_text",
                "handoff_pr_number",
                "continuation_branch",
                "continuation_pr_url",
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE builder_tasks ADD COLUMN {name} TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_builder_tasks_status ON builder_tasks(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_builder_tasks_requester "
                "ON builder_tasks(requester_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_builder_tasks_thread "
                "ON builder_tasks(channel_id, thread_ts, created_at)"
            )

    def enqueue(
        self,
        *,
        goal: str,
        requester_id: str | None,
        channel_id: str,
        thread_ts: str | None,
        handoff_pr_number: str | None = None,
    ) -> str:
        """Queue a turn and inherit the latest published PR in the same thread.

        An explicit external PR handoff always wins over implicit thread
        continuation. After the handoff succeeds, its PR URL/branch are stored
        on the task and later replies naturally continue that same PR.
        """
        task_id = "bld_" + uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        continuation_branch: str | None = None
        continuation_pr_url: str | None = None
        with connect(self.path) as conn:
            if thread_ts and not handoff_pr_number:
                previous = conn.execute(
                    """
                    SELECT branch_name, pr_url
                    FROM builder_tasks
                    WHERE channel_id=? AND thread_ts=?
                      AND status='succeeded'
                      AND branch_name IS NOT NULL
                      AND pr_url IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (channel_id, thread_ts),
                ).fetchone()
                if previous:
                    continuation_branch = previous["branch_name"]
                    continuation_pr_url = previous["pr_url"]

            conn.execute(
                """INSERT INTO builder_tasks
                (task_id, goal, requester_id, channel_id, thread_ts, status,
                 handoff_pr_number, continuation_branch, continuation_pr_url,
                 attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, 0, ?, ?)""",
                (
                    task_id,
                    goal,
                    requester_id,
                    channel_id,
                    thread_ts,
                    handoff_pr_number,
                    continuation_branch,
                    continuation_pr_url,
                    now,
                    now,
                ),
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
        """Atomically claim the oldest pending turn, flipping it to running."""
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

    def set_progress_message_ts(self, task_id: str, message_ts: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                "UPDATE builder_tasks SET progress_message_ts=?, updated_at=? WHERE task_id=?",
                (message_ts, now, task_id),
            )

    def mark_succeeded(
        self,
        task_id: str,
        *,
        pr_url: str | None = None,
        result_text: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                """UPDATE builder_tasks
                SET status='succeeded', pr_url=?, result_text=?, updated_at=?, finished_at=?
                WHERE task_id=?""",
                (pr_url, result_text, now, now, task_id),
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
        """Cancel a turn while it is still pending. Returns False if it was not pending."""
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            cursor = conn.execute(
                "UPDATE builder_tasks SET status='cancelled', updated_at=?, finished_at=? "
                "WHERE task_id=? AND status='pending'",
                (now, now, task_id),
            )
        return cursor.rowcount > 0

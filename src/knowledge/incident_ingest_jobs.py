"""Durable Create Knowledge queue for heavyweight incident snapshot imports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.core.database import connect


class IncidentIngestJobStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        with connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS incident_ingest_jobs (
                    job_id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    requester_id TEXT,
                    channel_id TEXT,
                    thread_ts TEXT,
                    progress_message_ts TEXT,
                    status TEXT NOT NULL,
                    progress_json TEXT,
                    metrics_json TEXT,
                    error_message TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            # Lightweight migration for databases created before progress UI existed.
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(incident_ingest_jobs)").fetchall()
            }
            if "progress_message_ts" not in columns:
                conn.execute("ALTER TABLE incident_ingest_jobs ADD COLUMN progress_message_ts TEXT")
            if "progress_json" not in columns:
                conn.execute("ALTER TABLE incident_ingest_jobs ADD COLUMN progress_json TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_incident_ingest_jobs_status ON incident_ingest_jobs(status)"
            )

    @staticmethod
    def _decode(row) -> dict[str, Any] | None:
        if not row:
            return None
        item = dict(row)
        item["metrics"] = json.loads(item.get("metrics_json") or "{}")
        item["progress"] = json.loads(item.get("progress_json") or "{}")
        return item

    def enqueue(
        self,
        *,
        file_path: str,
        file_name: str,
        requester_id: str | None,
        channel_id: str | None,
        thread_ts: str | None,
    ) -> str:
        job_id = "ING-" + uuid4().hex[:10].upper()
        now = datetime.now(timezone.utc).isoformat()
        initial_progress = json.dumps({"stage": "queued"}, sort_keys=True)
        with connect(self.path) as conn:
            conn.execute(
                """INSERT INTO incident_ingest_jobs
                (job_id,file_path,file_name,requester_id,channel_id,thread_ts,status,progress_json,attempts,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'pending',?,0,?,?)""",
                (
                    job_id,
                    file_path,
                    file_name,
                    requester_id,
                    channel_id,
                    thread_ts,
                    initial_progress,
                    now,
                    now,
                ),
            )
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM incident_ingest_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return self._decode(row)

    def set_progress_message(self, job_id: str, message_ts: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                "UPDATE incident_ingest_jobs SET progress_message_ts=?, updated_at=? WHERE job_id=?",
                (message_ts, now, job_id),
            )

    def update_progress(self, job_id: str, progress: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                "UPDATE incident_ingest_jobs SET progress_json=?, updated_at=? WHERE job_id=?",
                (json.dumps(progress, sort_keys=True), now, job_id),
            )

    def claim_next(self) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    UPDATE incident_ingest_jobs
                    SET status='running', started_at=?, updated_at=?, attempts=attempts+1
                    WHERE job_id=(
                        SELECT job_id FROM incident_ingest_jobs
                        WHERE status='pending' ORDER BY created_at LIMIT 1
                    )
                    RETURNING *
                    """,
                    (now, now),
                ).fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self._decode(row)

    def mark_succeeded(self, job_id: str, metrics: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                """UPDATE incident_ingest_jobs
                SET status='succeeded', metrics_json=?, progress_json=?, updated_at=?, finished_at=?
                WHERE job_id=?""",
                (
                    json.dumps(metrics, sort_keys=True),
                    json.dumps({"stage": "completed", "metrics": metrics}, sort_keys=True),
                    now,
                    now,
                    job_id,
                ),
            )

    def mark_failed(self, job_id: str, error_message: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        progress = {"stage": "failed", "error": error_message[:500]}
        with connect(self.path) as conn:
            conn.execute(
                """UPDATE incident_ingest_jobs
                SET status='failed', error_message=?, progress_json=?, updated_at=?, finished_at=?
                WHERE job_id=?""",
                (
                    error_message[:2000],
                    json.dumps(progress, sort_keys=True),
                    now,
                    now,
                    job_id,
                ),
            )

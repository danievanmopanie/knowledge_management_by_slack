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
                    status TEXT NOT NULL,
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_incident_ingest_jobs_status ON incident_ingest_jobs(status)"
            )

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
        with connect(self.path) as conn:
            conn.execute(
                """INSERT INTO incident_ingest_jobs
                (job_id,file_path,file_name,requester_id,channel_id,thread_ts,status,attempts,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'pending',0,?,?)""",
                (job_id, file_path, file_name, requester_id, channel_id, thread_ts, now, now),
            )
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM incident_ingest_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["metrics"] = json.loads(item.get("metrics_json") or "{}")
        return item

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
        return dict(row) if row else None

    def mark_succeeded(self, job_id: str, metrics: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                """UPDATE incident_ingest_jobs
                SET status='succeeded', metrics_json=?, updated_at=?, finished_at=?
                WHERE job_id=?""",
                (json.dumps(metrics, sort_keys=True), now, now, job_id),
            )

    def mark_failed(self, job_id: str, error_message: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.path) as conn:
            conn.execute(
                """UPDATE incident_ingest_jobs
                SET status='failed', error_message=?, updated_at=?, finished_at=?
                WHERE job_id=?""",
                (error_message[:2000], now, now, job_id),
            )

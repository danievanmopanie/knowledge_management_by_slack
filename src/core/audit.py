"""Durable, content-minimising audit trail for platform actions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.core.context import RequestContext
from src.core.database import connect


class AuditStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        with connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    actor_id TEXT,
                    channel_id TEXT,
                    thread_ts TEXT,
                    action TEXT NOT NULL,
                    target_type TEXT,
                    target_id TEXT,
                    outcome TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_events(request_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor_id)")

    def record(
        self,
        context: RequestContext,
        *,
        action: str,
        outcome: str,
        target_type: str = "",
        target_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        event_id = uuid4().hex
        with connect(self.path) as conn:
            conn.execute(
                """INSERT INTO audit_events
                (event_id, request_id, actor_id, channel_id, thread_ts, action,
                 target_type, target_id, outcome, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    context.request_id,
                    context.user_id,
                    context.channel_id,
                    context.thread_ts,
                    action,
                    target_type,
                    target_id,
                    outcome,
                    json.dumps(metadata or {}, sort_keys=True),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return event_id

    def by_request(self, request_id: str) -> list[dict[str, Any]]:
        with connect(self.path) as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE request_id=? ORDER BY created_at", (request_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def by_actor(self, actor_id: str) -> list[dict[str, Any]]:
        with connect(self.path) as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE actor_id=? ORDER BY created_at", (actor_id,)
            ).fetchall()
        return [dict(row) for row in rows]

"""Durable storage for knowledge-edit requests flagged from #frontend-support."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.core.database import connect


class EditRequestStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        with connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS frontend_knowledge_edit_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_channel_id TEXT NOT NULL,
                    source_thread_ts TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    document_title TEXT NOT NULL,
                    edit_note TEXT NOT NULL,
                    proposed_text TEXT NOT NULL DEFAULT '',
                    requested_by TEXT NOT NULL,
                    assignee_id TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    published_document_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def create(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        document_id: str,
        document_title: str,
        edit_note: str,
        proposed_text: str,
        requested_by: str,
    ) -> dict[str, Any]:
        with connect(self.path) as conn:
            cursor = conn.execute(
                """INSERT INTO frontend_knowledge_edit_requests
                (source_channel_id, source_thread_ts, document_id, document_title,
                 edit_note, proposed_text, requested_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (channel_id, thread_ts, document_id, document_title, edit_note, proposed_text, requested_by),
            )
            request_id = cursor.lastrowid
        task = self.get(request_id)
        if not task:
            raise RuntimeError("Knowledge edit request was not created")
        return task

    def get(self, request_id: int) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM frontend_knowledge_edit_requests WHERE id=?", (request_id,)
            ).fetchone()
        if not row:
            return None
        task = dict(row)
        task["request_id"] = f"KE-{int(task['id']):05d}"
        return task

    def assign(self, request_id: int, assignee_id: str) -> None:
        with connect(self.path) as conn:
            conn.execute(
                "UPDATE frontend_knowledge_edit_requests SET assignee_id=?, status='assigned' WHERE id=?",
                (assignee_id, request_id),
            )

    def mark_published(self, request_id: int, *, published_document_id: str) -> None:
        with connect(self.path) as conn:
            conn.execute(
                """UPDATE frontend_knowledge_edit_requests
                SET status='published', published_document_id=? WHERE id=?""",
                (published_document_id, request_id),
            )

    def dismiss(self, request_id: int) -> None:
        with connect(self.path) as conn:
            conn.execute(
                "UPDATE frontend_knowledge_edit_requests SET status='dismissed' WHERE id=?", (request_id,)
            )

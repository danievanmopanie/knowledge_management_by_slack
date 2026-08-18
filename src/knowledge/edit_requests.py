"""Durable knowledge-edit tasks for existing governed articles.

Creating a task is intentionally cheap: the request is persisted immediately in
``drafting`` state and can be rendered in Slack before any LLM work starts.  The
same task is later updated with the drafted revision.
"""

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
                    base_version_id TEXT NOT NULL,
                    edit_note TEXT NOT NULL,
                    proposed_text TEXT NOT NULL DEFAULT '',
                    requested_by TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'drafting',
                    shared_channel_id TEXT,
                    shared_message_ts TEXT,
                    published_document_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def create_drafting(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        document_id: str,
        document_title: str,
        base_version_id: str,
        edit_note: str,
        requested_by: str,
    ) -> dict[str, Any]:
        with connect(self.path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO frontend_knowledge_edit_requests
                    (source_channel_id, source_thread_ts, document_id,
                     document_title, base_version_id, edit_note, requested_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    thread_ts,
                    document_id,
                    document_title,
                    base_version_id,
                    edit_note.strip(),
                    requested_by,
                ),
            )
            request_id = int(cursor.lastrowid)
        task = self.get(request_id)
        if task is None:
            raise RuntimeError("Knowledge edit request was not persisted")
        return task

    def get(self, request_id: int) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM frontend_knowledge_edit_requests WHERE id=?",
                (request_id,),
            ).fetchone()
        if not row:
            return None
        task = dict(row)
        task["request_id"] = f"KE-{int(task['id']):05d}"
        return task

    def attach_shared_message(self, request_id: int, *, channel_id: str, message_ts: str) -> None:
        with connect(self.path) as conn:
            conn.execute(
                """
                UPDATE frontend_knowledge_edit_requests
                SET shared_channel_id=?, shared_message_ts=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (channel_id, message_ts, request_id),
            )

    def set_draft(self, request_id: int, proposed_text: str) -> None:
        with connect(self.path) as conn:
            conn.execute(
                """
                UPDATE frontend_knowledge_edit_requests
                SET proposed_text=?, status='review', updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='drafting'
                """,
                (proposed_text.strip(), request_id),
            )

    def mark_published(self, request_id: int, *, published_document_id: str) -> None:
        with connect(self.path) as conn:
            conn.execute(
                """
                UPDATE frontend_knowledge_edit_requests
                SET status='published', published_document_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (published_document_id, request_id),
            )

    def dismiss(self, request_id: int) -> None:
        with connect(self.path) as conn:
            conn.execute(
                """
                UPDATE frontend_knowledge_edit_requests
                SET status='dismissed', updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (request_id,),
            )

"""Durable knowledge-edit tasks for existing governed articles.

Creating a task is intentionally cheap: the request is persisted immediately in
``drafting`` state and can be rendered in Slack before any LLM work starts. The
same task is later updated with the drafted revision and final review decision.
"""

from __future__ import annotations

import json
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
                    published_version_id TEXT,
                    decided_by TEXT,
                    error_message TEXT NOT NULL DEFAULT '',
                    applied_review_ids_json TEXT NOT NULL DEFAULT '[]',
                    feedback_review_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(frontend_knowledge_edit_requests)")
            }
            migrations = {
                "error_message": "TEXT NOT NULL DEFAULT ''",
                "published_version_id": "TEXT",
                "decided_by": "TEXT",
                "applied_review_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "feedback_review_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            }
            for column, ddl in migrations.items():
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE frontend_knowledge_edit_requests ADD COLUMN {column} {ddl}"
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
        task["applied_review_ids"] = [
            int(item) for item in json.loads(task.pop("applied_review_ids_json") or "[]")
        ]
        task["feedback_review_ids"] = [
            int(item) for item in json.loads(task.pop("feedback_review_ids_json") or "[]")
        ]
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
                SET proposed_text=?, status='review', error_message='', updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='drafting'
                """,
                (proposed_text.strip(), request_id),
            )

    def start_feedback_draft(self, request_id: int, *, review_ids: list[int]) -> bool:
        """Move a reviewable task into explicit owner-triggered feedback drafting."""
        review_ids = sorted({int(item) for item in review_ids if int(item) > 0})
        if not review_ids:
            return False
        with connect(self.path) as conn:
            cursor = conn.execute(
                """
                UPDATE frontend_knowledge_edit_requests
                SET status='feedback_drafting', feedback_review_ids_json=?,
                    error_message='', updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='review'
                """,
                (json.dumps(review_ids), request_id),
            )
        return cursor.rowcount == 1

    def set_feedback_draft(self, request_id: int, proposed_text: str) -> None:
        """Persist the feedback-updated draft and mark those review inputs as applied."""
        with connect(self.path) as conn:
            row = conn.execute(
                """
                SELECT applied_review_ids_json, feedback_review_ids_json
                FROM frontend_knowledge_edit_requests
                WHERE id=? AND status='feedback_drafting'
                """,
                (request_id,),
            ).fetchone()
            if not row:
                return
            applied = {int(item) for item in json.loads(row["applied_review_ids_json"] or "[]")}
            pending = {int(item) for item in json.loads(row["feedback_review_ids_json"] or "[]")}
            conn.execute(
                """
                UPDATE frontend_knowledge_edit_requests
                SET proposed_text=?, status='review', error_message='',
                    applied_review_ids_json=?, feedback_review_ids_json='[]',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='feedback_drafting'
                """,
                (proposed_text.strip(), json.dumps(sorted(applied | pending)), request_id),
            )

    def mark_failed(self, request_id: int, *, error_message: str) -> None:
        with connect(self.path) as conn:
            conn.execute(
                """
                UPDATE frontend_knowledge_edit_requests
                SET status='draft_failed', error_message=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status IN ('drafting', 'feedback_drafting')
                """,
                (error_message.strip()[:1200], request_id),
            )

    def mark_stale(self, request_id: int, *, decided_by: str) -> None:
        with connect(self.path) as conn:
            conn.execute(
                """
                UPDATE frontend_knowledge_edit_requests
                SET status='stale', decided_by=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='review'
                """,
                (decided_by, request_id),
            )

    def mark_published(
        self,
        request_id: int,
        *,
        published_document_id: str,
        published_version_id: str,
        decided_by: str,
    ) -> None:
        with connect(self.path) as conn:
            conn.execute(
                """
                UPDATE frontend_knowledge_edit_requests
                SET status='published', published_document_id=?, published_version_id=?,
                    decided_by=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='review'
                """,
                (published_document_id, published_version_id, decided_by, request_id),
            )

    def dismiss(self, request_id: int, *, decided_by: str = "") -> None:
        with connect(self.path) as conn:
            conn.execute(
                """
                UPDATE frontend_knowledge_edit_requests
                SET status='dismissed', decided_by=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status IN ('drafting', 'feedback_drafting', 'review', 'draft_failed', 'stale')
                """,
                (decided_by or None, request_id),
            )

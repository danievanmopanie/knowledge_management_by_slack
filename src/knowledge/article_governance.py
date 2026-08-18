"""Durable ownership and technical-review state for governed knowledge articles.

This module deliberately contains no Slack or LLM logic. Ownership and review
routing must be fast, deterministic and auditable; Slack DMs/UI sit on top of
this store in a separate layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.core.database import connect


class ArticleGovernanceStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        with connect(path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_article_ownership (
                    document_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    assigned_by_user_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS knowledge_article_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    reviewer_user_id TEXT NOT NULL,
                    requested_by_user_id TEXT NOT NULL,
                    review_note TEXT NOT NULL DEFAULT '',
                    response_note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'requested',
                    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_review_one_pending
                    ON knowledge_article_reviews(document_id, version_id, reviewer_user_id)
                    WHERE status='requested';

                CREATE INDEX IF NOT EXISTS idx_knowledge_reviews_reviewer_status
                    ON knowledge_article_reviews(reviewer_user_id, status);
                CREATE INDEX IF NOT EXISTS idx_knowledge_reviews_document_status
                    ON knowledge_article_reviews(document_id, version_id, status);
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(knowledge_article_reviews)")
            }
            if "response_note" not in columns:
                conn.execute(
                    "ALTER TABLE knowledge_article_reviews "
                    "ADD COLUMN response_note TEXT NOT NULL DEFAULT ''"
                )

    def assign_owner(
        self,
        *,
        document_id: str,
        owner_user_id: str,
        assigned_by_user_id: str,
    ) -> dict[str, Any]:
        with connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO knowledge_article_ownership
                    (document_id, owner_user_id, assigned_by_user_id)
                VALUES (?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    owner_user_id=excluded.owner_user_id,
                    assigned_by_user_id=excluded.assigned_by_user_id,
                    assigned_at=CURRENT_TIMESTAMP
                """,
                (document_id, owner_user_id, assigned_by_user_id),
            )
        owner = self.get_owner(document_id)
        if owner is None:
            raise RuntimeError("Article owner assignment was not persisted")
        return owner

    def get_owner(self, document_id: str) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_article_ownership WHERE document_id=?",
                (document_id,),
            ).fetchone()
        return dict(row) if row else None

    def request_review(
        self,
        *,
        document_id: str,
        version_id: str,
        reviewer_user_id: str,
        requested_by_user_id: str,
        review_note: str = "",
    ) -> dict[str, Any]:
        with connect(self.path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO knowledge_article_reviews
                    (document_id, version_id, reviewer_user_id,
                     requested_by_user_id, review_note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    version_id,
                    reviewer_user_id,
                    requested_by_user_id,
                    review_note.strip(),
                ),
            )
            review_id = int(cursor.lastrowid)
        review = self.get_review(review_id)
        if review is None:
            raise RuntimeError("Article review assignment was not persisted")
        return review

    def get_review(self, review_id: int) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_article_reviews WHERE id=?",
                (review_id,),
            ).fetchone()
        return dict(row) if row else None

    def complete_review(
        self,
        review_id: int,
        *,
        reviewer_user_id: str,
        response_note: str,
    ) -> dict[str, Any] | None:
        """Complete a pending review only when the submitting user owns that task."""
        response = response_note.strip()
        if not response:
            raise ValueError("Technical review input is required")
        with connect(self.path) as conn:
            cursor = conn.execute(
                """
                UPDATE knowledge_article_reviews
                SET status='completed', response_note=?, completed_at=CURRENT_TIMESTAMP
                WHERE id=? AND reviewer_user_id=? AND status='requested'
                """,
                (response[:4000], review_id, reviewer_user_id),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_review(review_id)

    def pending_reviews_for(self, reviewer_user_id: str) -> list[dict[str, Any]]:
        with connect(self.path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM knowledge_article_reviews
                WHERE reviewer_user_id=? AND status='requested'
                ORDER BY requested_at, id
                """,
                (reviewer_user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_reviews_for_article(
        self,
        document_id: str,
        *,
        version_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with connect(self.path) as conn:
            if version_id:
                rows = conn.execute(
                    """
                    SELECT * FROM knowledge_article_reviews
                    WHERE document_id=? AND version_id=? AND status='requested'
                    ORDER BY requested_at, id
                    """,
                    (document_id, version_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM knowledge_article_reviews
                    WHERE document_id=? AND status='requested'
                    ORDER BY requested_at, id
                    """,
                    (document_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def completed_reviews_for_article(
        self,
        document_id: str,
        *,
        version_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        with connect(self.path) as conn:
            if version_id:
                rows = conn.execute(
                    """
                    SELECT * FROM knowledge_article_reviews
                    WHERE document_id=? AND version_id=? AND status='completed'
                    ORDER BY completed_at DESC, id DESC LIMIT ?
                    """,
                    (document_id, version_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM knowledge_article_reviews
                    WHERE document_id=? AND status='completed'
                    ORDER BY completed_at DESC, id DESC LIMIT ?
                    """,
                    (document_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

"""Small durable memory of governed knowledge surfaced to a support thread.

This exists so a later phrase such as "that article is outdated" can resolve to
what the technician was actually shown without another retrieval or LLM call.
The set is replaced on each governed answer and kept deliberately tiny.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.database import connect


class CitationMemory:
    def __init__(self, path: Path | None = None):
        self.path = path
        with connect(path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS frontend_recent_citations (
                    channel_id TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    cited_at TEXT NOT NULL,
                    PRIMARY KEY (channel_id, thread_ts, document_id)
                );
                CREATE INDEX IF NOT EXISTS idx_frontend_recent_citations_thread
                    ON frontend_recent_citations(channel_id, thread_ts, rank);
                """
            )

    def replace(self, *, channel_id: str, thread_ts: str, articles: list[dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        compact: list[tuple[str, str]] = []
        seen: set[str] = set()
        for article in articles:
            document_id = str(article.get("document_id") or "").strip()
            if not document_id or document_id in seen:
                continue
            seen.add(document_id)
            compact.append((document_id, str(article.get("title") or document_id)))
            if len(compact) >= 3:
                break

        with connect(self.path) as conn:
            conn.execute(
                "DELETE FROM frontend_recent_citations WHERE channel_id=? AND thread_ts=?",
                (channel_id, thread_ts),
            )
            conn.executemany(
                """
                INSERT INTO frontend_recent_citations
                    (channel_id, thread_ts, document_id, title, rank, cited_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (channel_id, thread_ts, document_id, title, rank, now)
                    for rank, (document_id, title) in enumerate(compact, start=1)
                ],
            )

    def recent(self, channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        with connect(self.path) as conn:
            rows = conn.execute(
                """
                SELECT document_id, title, rank, cited_at
                FROM frontend_recent_citations
                WHERE channel_id=? AND thread_ts=?
                ORDER BY rank ASC
                """,
                (channel_id, thread_ts),
            ).fetchall()
        return [dict(row) for row in rows]

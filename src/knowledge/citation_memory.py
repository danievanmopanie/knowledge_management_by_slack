"""Remember which governed knowledge document was last cited to a Slack thread.

`#frontend-support` answers cite governed knowledge inline (see
`src/knowledge/citations.py`), but nothing previously remembered *which* document
that was once the reply was sent. That makes "that article is outdated, can we fix
it?" impossible to act on without asking the technician to repeat the title. This
is a small, standalone store so a knowledge-edit request triggered from
`#frontend-support` can resolve "that article" back to the document it actually
means.
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS frontend_last_citation (
                    channel_id TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    cited_at TEXT NOT NULL,
                    PRIMARY KEY (channel_id, thread_ts)
                )
                """
            )

    def record(self, *, channel_id: str, thread_ts: str, document_id: str, title: str) -> None:
        if not document_id:
            return
        with connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO frontend_last_citation (channel_id, thread_ts, document_id, title, cited_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, thread_ts) DO UPDATE SET
                    document_id=excluded.document_id, title=excluded.title, cited_at=excluded.cited_at
                """,
                (channel_id, thread_ts, document_id, title, datetime.now(timezone.utc).isoformat()),
            )

    def get(self, channel_id: str, thread_ts: str) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM frontend_last_citation WHERE channel_id=? AND thread_ts=?",
                (channel_id, thread_ts),
            ).fetchone()
        return dict(row) if row else None

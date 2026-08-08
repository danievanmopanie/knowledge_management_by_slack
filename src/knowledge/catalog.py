"""Versioned knowledge-document catalogue backed by SQLite."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.database import connect


def document_id(source_system: str, source_id: str) -> str:
    raw = f"{source_system}:{source_id}".encode("utf-8")
    return "doc_" + hashlib.sha256(raw).hexdigest()[:20]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class KnowledgeCatalog:
    def __init__(self, path: Path | None = None):
        self.path = path
        with connect(path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    source_system TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    owner_id TEXT,
                    visibility TEXT NOT NULL DEFAULT 'internal',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_system, source_id)
                );
                CREATE TABLE IF NOT EXISTS document_versions (
                    version_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(document_id),
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    chunk_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    superseded_at TEXT,
                    UNIQUE(document_id, content_hash)
                );
                """
            )

    def active_version(self, doc_id: str) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute(
                "SELECT * FROM document_versions WHERE document_id=? AND status='active'",
                (doc_id,),
            ).fetchone()
        return dict(row) if row else None

    def register_version(
        self,
        *,
        source_system: str,
        source_id: str,
        title: str,
        text: str,
        owner_id: str | None,
        visibility: str = "internal",
    ) -> dict[str, Any]:
        doc_id = document_id(source_system, source_id)
        digest = content_hash(text)
        version_id = f"{doc_id}_v_{digest[:16]}"
        now = datetime.now(timezone.utc).isoformat()

        with connect(self.path) as conn:
            conn.execute(
                """INSERT INTO documents
                (document_id, source_system, source_id, title, owner_id, visibility, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    title=excluded.title, owner_id=excluded.owner_id,
                    visibility=excluded.visibility, updated_at=excluded.updated_at""",
                (doc_id, source_system, source_id, title, owner_id, visibility, now, now),
            )
            active = conn.execute(
                "SELECT * FROM document_versions WHERE document_id=? AND status='active'",
                (doc_id,),
            ).fetchone()
            if active and active["content_hash"] == digest:
                result = dict(active)
                result["document_id"] = doc_id
                result["unchanged"] = True
                result["old_chunk_ids"] = json.loads(active["chunk_ids_json"] or "[]")
                return result

            old_chunk_ids: list[str] = []
            if active:
                old_chunk_ids = json.loads(active["chunk_ids_json"] or "[]")
                conn.execute(
                    "UPDATE document_versions SET status='superseded', superseded_at=? WHERE version_id=?",
                    (now, active["version_id"]),
                )

            conn.execute(
                """INSERT OR REPLACE INTO document_versions
                (version_id, document_id, content_hash, status, chunk_ids_json, created_at)
                VALUES (?, ?, ?, 'active', '[]', ?)""",
                (version_id, doc_id, digest, now),
            )

        return {
            "document_id": doc_id,
            "version_id": version_id,
            "content_hash": digest,
            "unchanged": False,
            "old_chunk_ids": old_chunk_ids,
        }

    def set_chunk_ids(self, version_id: str, chunk_ids: list[str]) -> None:
        with connect(self.path) as conn:
            conn.execute(
                "UPDATE document_versions SET chunk_ids_json=? WHERE version_id=?",
                (json.dumps(chunk_ids), version_id),
            )

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        with connect(self.path) as conn:
            row = conn.execute("SELECT * FROM documents WHERE document_id=?", (doc_id,)).fetchone()
        return dict(row) if row else None

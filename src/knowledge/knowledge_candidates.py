"""Durable human-review workflow for turning incident evidence into governed knowledge.

A Knowledge Candidate is intentionally not searchable knowledge.  It is the
workflow object between support evidence and an approved governed document.
Frontend Support may create/flag a candidate; Create Knowledge owns enrichment,
review and publication.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.config import settings
from src.knowledge.governed_ingest import commit_knowledge

ACTIVE_STATUSES = {
    "needs_enrichment",
    "in_progress",
    "needs_input",
    "ready_for_review",
    "changes_requested",
}
REVIEW_REQUIRED_FIELDS = ("applies_when", "procedure", "validation")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _candidate_id(row_id: int) -> str:
    return f"KC-{row_id:06d}"


def _row_id(candidate_id: str) -> int:
    value = str(candidate_id or "").strip().upper()
    if not value.startswith("KC-"):
        raise ValueError(f"Invalid knowledge candidate id: {candidate_id}")
    return int(value.split("-", 1)[1])


@dataclass(frozen=True)
class KnowledgeCandidate:
    id: int
    candidate_id: str
    incident_number: str
    title: str
    issue_pattern: str
    symptom: str
    recorded_resolution: str
    resolution_pattern: str
    recorded_root_cause: str
    applies_when: str
    procedure: str
    validation: str
    prerequisites: str
    notes: str
    knowledge_gap: str
    status: str
    requested_by: str
    owner_id: str
    reviewer_id: str
    source_channel_id: str
    source_thread_ts: str
    source_message_ts: str
    create_knowledge_message_ts: str
    input_request: str
    input_requested_from: str
    published_document_id: str
    published_version_id: str
    created_at: str
    updated_at: str

    @property
    def missing_review_fields(self) -> tuple[str, ...]:
        return tuple(name for name in REVIEW_REQUIRED_FIELDS if not getattr(self, name).strip())

    @property
    def ready_for_review(self) -> bool:
        return not self.missing_review_fields

    def publication_text(self) -> str:
        """Render only reviewed candidate content into governed knowledge."""
        sections = [
            f"# {self.title}",
            f"Knowledge candidate: {self.candidate_id}",
            f"Source incident: {self.incident_number}" if self.incident_number else "",
            "",
            "## When this applies",
            self.applies_when,
            "",
            "## Resolution procedure",
            self.procedure,
            "",
            "## Validation",
            self.validation,
        ]
        if self.prerequisites.strip():
            sections.extend(["", "## Prerequisites / risks", self.prerequisites])
        if self.recorded_root_cause.strip():
            sections.extend(["", "## Confirmed root-cause evidence", self.recorded_root_cause])
        if self.notes.strip():
            sections.extend(["", "## Additional notes", self.notes])
        if self.issue_pattern.strip() or self.symptom.strip() or self.resolution_pattern.strip():
            sections.extend(["", "## Provenance from incident evidence"])
            if self.issue_pattern.strip():
                sections.append(f"Issue pattern: {self.issue_pattern}")
            if self.symptom.strip():
                sections.append(f"Symptom: {self.symptom}")
            if self.resolution_pattern.strip():
                sections.append(f"Recorded resolution pattern: {self.resolution_pattern}")
        return "\n".join(part for part in sections if part is not None).strip()


class KnowledgeCandidateStore:
    """SQLite-backed candidate backlog shared by the two Slack app runtimes."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or settings.platform_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_number TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    issue_pattern TEXT NOT NULL DEFAULT '',
                    symptom TEXT NOT NULL DEFAULT '',
                    recorded_resolution TEXT NOT NULL DEFAULT '',
                    resolution_pattern TEXT NOT NULL DEFAULT '',
                    recorded_root_cause TEXT NOT NULL DEFAULT '',
                    applies_when TEXT NOT NULL DEFAULT '',
                    procedure TEXT NOT NULL DEFAULT '',
                    validation TEXT NOT NULL DEFAULT '',
                    prerequisites TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    knowledge_gap TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'needs_enrichment',
                    requested_by TEXT NOT NULL DEFAULT '',
                    owner_id TEXT NOT NULL DEFAULT '',
                    reviewer_id TEXT NOT NULL DEFAULT '',
                    source_channel_id TEXT NOT NULL DEFAULT '',
                    source_thread_ts TEXT NOT NULL DEFAULT '',
                    source_message_ts TEXT NOT NULL DEFAULT '',
                    create_knowledge_message_ts TEXT NOT NULL DEFAULT '',
                    input_request TEXT NOT NULL DEFAULT '',
                    input_requested_from TEXT NOT NULL DEFAULT '',
                    published_document_id TEXT NOT NULL DEFAULT '',
                    published_version_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_candidates_status
                    ON knowledge_candidates(status, id);
                CREATE INDEX IF NOT EXISTS idx_knowledge_candidates_incident
                    ON knowledge_candidates(incident_number, status);
                """
            )

    @staticmethod
    def _candidate(row: sqlite3.Row | None) -> KnowledgeCandidate | None:
        if row is None:
            return None
        values = dict(row)
        values["candidate_id"] = _candidate_id(int(values["id"]))
        return KnowledgeCandidate(**values)

    def get(self, candidate_id: str) -> KnowledgeCandidate | None:
        try:
            row_id = _row_id(candidate_id)
        except (TypeError, ValueError):
            return None
        with self._connect() as conn:
            return self._candidate(conn.execute("SELECT * FROM knowledge_candidates WHERE id = ?", (row_id,)).fetchone())

    def find_active_for_incident(self, incident_number: str) -> KnowledgeCandidate | None:
        number = str(incident_number or "").strip().upper()
        if not number:
            return None
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        params = [number, *sorted(ACTIVE_STATUSES)]
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM knowledge_candidates WHERE incident_number = ? "
                f"AND status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
                params,
            ).fetchone()
        return self._candidate(row)

    def create(
        self,
        *,
        incident_number: str,
        title: str,
        issue_pattern: str = "",
        symptom: str = "",
        recorded_resolution: str = "",
        resolution_pattern: str = "",
        recorded_root_cause: str = "",
        applies_when: str = "",
        procedure: str = "",
        validation: str = "",
        prerequisites: str = "",
        notes: str = "",
        knowledge_gap: str = "",
        requested_by: str = "",
        source_channel_id: str = "",
        source_thread_ts: str = "",
        source_message_ts: str = "",
        deduplicate: bool = True,
    ) -> tuple[KnowledgeCandidate, bool]:
        number = str(incident_number or "").strip().upper()
        if deduplicate and number:
            existing = self.find_active_for_incident(number)
            if existing is not None:
                return existing, False
        now = _now()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO knowledge_candidates (
                    incident_number, title, issue_pattern, symptom,
                    recorded_resolution, resolution_pattern, recorded_root_cause,
                    applies_when, procedure, validation, prerequisites, notes,
                    knowledge_gap, requested_by, source_channel_id,
                    source_thread_ts, source_message_ts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    number, title.strip(), issue_pattern.strip(), symptom.strip(),
                    recorded_resolution.strip(), resolution_pattern.strip(), recorded_root_cause.strip(),
                    applies_when.strip(), procedure.strip(), validation.strip(), prerequisites.strip(), notes.strip(),
                    knowledge_gap.strip(), requested_by.strip(), source_channel_id.strip(),
                    source_thread_ts.strip(), source_message_ts.strip(), now, now,
                ),
            )
            row_id = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM knowledge_candidates WHERE id = ?", (row_id,)).fetchone()
        candidate = self._candidate(row)
        assert candidate is not None
        return candidate, True

    def update_content(self, candidate_id: str, **fields: str) -> KnowledgeCandidate:
        allowed = {
            "title", "applies_when", "procedure", "validation", "prerequisites",
            "notes", "knowledge_gap", "recorded_root_cause",
        }
        updates = {key: str(value or "").strip() for key, value in fields.items() if key in allowed}
        if not updates:
            candidate = self.get(candidate_id)
            if candidate is None:
                raise KeyError(candidate_id)
            return candidate
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = [*updates.values(), _row_id(candidate_id)]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE knowledge_candidates SET {assignments} WHERE id = ?", values)
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        return candidate

    def set_status(
        self,
        candidate_id: str,
        status: str,
        *,
        owner_id: str | None = None,
        reviewer_id: str | None = None,
    ) -> KnowledgeCandidate:
        updates: dict[str, Any] = {"status": status, "updated_at": _now()}
        if owner_id is not None:
            updates["owner_id"] = owner_id
        if reviewer_id is not None:
            updates["reviewer_id"] = reviewer_id
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE knowledge_candidates SET {assignments} WHERE id = ?",
                [*updates.values(), _row_id(candidate_id)],
            )
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        return candidate

    def claim(self, candidate_id: str, user_id: str) -> KnowledgeCandidate:
        return self.set_status(candidate_id, "in_progress", owner_id=user_id)

    def mark_ready(self, candidate_id: str) -> KnowledgeCandidate:
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        if candidate.missing_review_fields:
            raise ValueError("Missing required knowledge fields: " + ", ".join(candidate.missing_review_fields))
        return self.set_status(candidate_id, "ready_for_review")

    def request_input(self, candidate_id: str, *, question: str, user_id: str = "") -> KnowledgeCandidate:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE knowledge_candidates SET status = 'needs_input', input_request = ?, "
                "input_requested_from = ?, updated_at = ? WHERE id = ?",
                (question.strip(), user_id.strip(), _now(), _row_id(candidate_id)),
            )
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        return candidate

    def request_changes(self, candidate_id: str, *, note: str, reviewer_id: str) -> KnowledgeCandidate:
        candidate = self.update_content(candidate_id, knowledge_gap=note)
        return self.set_status(candidate.candidate_id, "changes_requested", reviewer_id=reviewer_id)

    def list_unposted(self, limit: int = 20) -> list[KnowledgeCandidate]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_candidates WHERE create_knowledge_message_ts = '' "
                "AND status != 'published' ORDER BY id ASC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [candidate for row in rows if (candidate := self._candidate(row)) is not None]

    def mark_posted(self, candidate_id: str, message_ts: str) -> KnowledgeCandidate:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE knowledge_candidates SET create_knowledge_message_ts = ?, updated_at = ? WHERE id = ?",
                (message_ts.strip(), _now(), _row_id(candidate_id)),
            )
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        return candidate

    def publish(self, candidate_id: str, *, reviewer_id: str) -> tuple[KnowledgeCandidate, dict[str, Any]]:
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        if candidate.status != "ready_for_review":
            raise ValueError("Candidate must be ready for review before publication")
        if candidate.missing_review_fields:
            raise ValueError("Missing required knowledge fields: " + ", ".join(candidate.missing_review_fields))

        result = commit_knowledge(
            text=candidate.publication_text(),
            title=candidate.title,
            source_id=candidate.candidate_id,
            owner_id=candidate.owner_id or candidate.requested_by or reviewer_id,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE knowledge_candidates
                SET status = 'published', reviewer_id = ?, published_document_id = ?,
                    published_version_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    reviewer_id,
                    str(result.get("document_id") or ""),
                    str(result.get("version_id") or ""),
                    _now(),
                    candidate.id,
                ),
            )
        published = self.get(candidate_id)
        assert published is not None
        return published, result

    def snapshot(self, candidate_id: str) -> str:
        candidate = self.get(candidate_id)
        if candidate is None:
            return "{}"
        return json.dumps(candidate.__dict__, sort_keys=True)

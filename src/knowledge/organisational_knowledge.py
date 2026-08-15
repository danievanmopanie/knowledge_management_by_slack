"""Persistent enriched support knowledge and cross-incident pattern retrieval.

The raw temporal incident store remains the evidence source of truth. This module
stores the structured interpretation of that evidence (symptom, actions,
resolution, root cause and reusable issue pattern) and builds a separate vector
index used only to *locate* symptomatically similar enriched cases.

Important design rule: counts and claims about repeated organisational knowledge
are computed deterministically from structured enriched records. The response LLM
never invents frequencies or success rates.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.core.config import settings
from src.core.database import connect
from src.knowledge.support_extraction import ExtractedAction, SupportExtraction
from src.knowledge.vectorstore import VectorStore
from src.reporting.incidents import Incident

KNOWLEDGE_COLLECTION = "support_knowledge"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalise_label(value: Any) -> str:
    text = _normalise_space(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _slug(value: Any) -> str:
    text = _normalise_label(value)
    return re.sub(r"\s+", "-", text).strip("-")


def _parse_dt(value: Any) -> datetime | None:
    text = _normalise_space(value)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _incident_from_record(record: dict[str, Any]) -> Incident:
    return Incident(
        number=_normalise_space(record.get("number")),
        short_description=_normalise_space(record.get("short_description")),
        description=_normalise_space(record.get("description")),
        work_notes=_normalise_space(record.get("work_notes")),
        comments=_normalise_space(record.get("comments")),
        resolution_notes=_normalise_space(record.get("resolution_notes")),
        state=_normalise_space(record.get("state")),
        priority=_normalise_space(record.get("priority")),
        assignment_group=_normalise_space(record.get("assignment_group")),
        assigned_to=_normalise_space(record.get("assigned_to")),
        caller=_normalise_space(record.get("caller")),
        location=_normalise_space(record.get("location")),
        configuration_item=_normalise_space(record.get("configuration_item")),
        opened_at=_parse_dt(record.get("opened_at")),
        updated_at=_parse_dt(record.get("updated_at")),
        resolved_at=_parse_dt(record.get("resolved_at")),
        closed_at=_parse_dt(record.get("closed_at")),
        category=_normalise_space(record.get("category")),
        subcategory=_normalise_space(record.get("subcategory")),
        raw=dict(record),
    )


def _pattern_label(extraction: SupportExtraction, incident: Incident) -> str:
    preferred = _normalise_space(extraction.issue_pattern)
    if preferred:
        return preferred
    symptom = _normalise_space(extraction.symptom)
    technology = next(
        (_normalise_space(value) for value in extraction.technologies if _normalise_space(value)),
        "",
    )
    if technology and symptom:
        return f"{technology}: {symptom}"
    return symptom


def _canonical_action(action: ExtractedAction) -> str:
    return _normalise_space(action.canonical_action or action.action)


def _resolution_label(extraction: SupportExtraction) -> str:
    return _normalise_space(extraction.resolution_pattern or extraction.resolution)


def _root_cause_label(extraction: SupportExtraction) -> str:
    return _normalise_space(extraction.root_cause_pattern or extraction.root_cause)


@dataclass(frozen=True)
class EnrichmentCandidate:
    incident: Incident
    row_hash: str
    source_file: str


@dataclass(frozen=True)
class EnrichedIncidentKnowledge:
    incident_number: str
    row_hash: str
    model: str
    source_file: str
    pattern_key: str
    pattern_label: str
    symptom: str
    resolution: str
    resolution_pattern: str
    root_cause: str
    root_cause_pattern: str
    technologies: tuple[str, ...]
    environments: tuple[str, ...]
    location: str
    assignment_group: str
    confidence: float
    extraction: SupportExtraction
    actions: tuple[ExtractedAction, ...]


class OrganisationalKnowledgeStore:
    """SQLite source of truth for enriched incident knowledge and pattern rollups."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or settings.platform_db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = connect(self.path)
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS support_incident_knowledge (
                    incident_number TEXT PRIMARY KEY,
                    row_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    source_file TEXT,
                    pattern_key TEXT,
                    pattern_label TEXT,
                    symptom TEXT,
                    resolution TEXT,
                    resolution_pattern TEXT,
                    root_cause TEXT,
                    root_cause_pattern TEXT,
                    technologies_json TEXT NOT NULL DEFAULT '[]',
                    environments_json TEXT NOT NULL DEFAULT '[]',
                    location TEXT,
                    assignment_group TEXT,
                    confidence REAL NOT NULL DEFAULT 0,
                    extraction_json TEXT NOT NULL,
                    enriched_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_support_incident_knowledge_pattern
                    ON support_incident_knowledge(pattern_key);
                CREATE INDEX IF NOT EXISTS idx_support_incident_knowledge_row_hash
                    ON support_incident_knowledge(row_hash, model);

                CREATE TABLE IF NOT EXISTS support_knowledge_actions (
                    incident_number TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    canonical_action TEXT,
                    outcome TEXT,
                    contributor TEXT,
                    confidence REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (incident_number, ordinal),
                    FOREIGN KEY (incident_number)
                        REFERENCES support_incident_knowledge(incident_number)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_support_knowledge_actions_outcome
                    ON support_knowledge_actions(outcome, canonical_action);

                CREATE TABLE IF NOT EXISTS support_patterns (
                    pattern_key TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    incident_count INTEGER NOT NULL,
                    resolved_count INTEGER NOT NULL,
                    avg_confidence REAL NOT NULL,
                    resolution_counts_json TEXT NOT NULL DEFAULT '[]',
                    successful_action_counts_json TEXT NOT NULL DEFAULT '[]',
                    failed_action_counts_json TEXT NOT NULL DEFAULT '[]',
                    root_cause_counts_json TEXT NOT NULL DEFAULT '[]',
                    technologies_json TEXT NOT NULL DEFAULT '[]',
                    environments_json TEXT NOT NULL DEFAULT '[]',
                    locations_json TEXT NOT NULL DEFAULT '[]',
                    incident_numbers_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );
                """
            )

    def pending_count(self, *, model: str | None = None) -> int:
        model_name = model or settings.support_extraction_model
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM incident_current c
                LEFT JOIN support_incident_knowledge k
                  ON UPPER(k.incident_number)=UPPER(c.number)
                WHERE k.incident_number IS NULL
                   OR k.row_hash <> c.row_hash
                   OR k.model <> ?
                """,
                (model_name,),
            ).fetchone()
        return int(row[0] if row else 0)

    def pending(self, *, limit: int = 20, model: str | None = None) -> list[EnrichmentCandidate]:
        model_name = model or settings.support_extraction_model
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.number, c.row_hash, c.record_json, c.source_file
                FROM incident_current c
                LEFT JOIN support_incident_knowledge k
                  ON UPPER(k.incident_number)=UPPER(c.number)
                WHERE k.incident_number IS NULL
                   OR k.row_hash <> c.row_hash
                   OR k.model <> ?
                ORDER BY c.latest_seen_at ASC, c.number ASC
                LIMIT ?
                """,
                (model_name, max(1, int(limit))),
            ).fetchall()

        candidates: list[EnrichmentCandidate] = []
        for row in rows:
            try:
                record = json.loads(row["record_json"] or "{}")
            except json.JSONDecodeError:
                continue
            incident = _incident_from_record(record)
            if not incident.number:
                incident.number = str(row["number"] or "").strip()
            candidates.append(
                EnrichmentCandidate(
                    incident=incident,
                    row_hash=str(row["row_hash"] or ""),
                    source_file=str(row["source_file"] or ""),
                )
            )
        return candidates

    def upsert(
        self,
        candidate: EnrichmentCandidate,
        extraction: SupportExtraction,
        *,
        model: str | None = None,
    ) -> EnrichedIncidentKnowledge:
        incident = candidate.incident
        model_name = model or settings.support_extraction_model
        pattern_label = _pattern_label(extraction, incident)
        pattern_key = _slug(pattern_label)
        resolution_pattern = _resolution_label(extraction)
        root_cause_pattern = _root_cause_label(extraction)
        technologies = tuple(
            value for value in (_normalise_space(v) for v in extraction.technologies) if value
        )
        environments = tuple(
            value for value in (_normalise_space(v) for v in extraction.environment) if value
        )
        actions = tuple(extraction.actions)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO support_incident_knowledge(
                    incident_number,row_hash,model,source_file,pattern_key,pattern_label,
                    symptom,resolution,resolution_pattern,root_cause,root_cause_pattern,
                    technologies_json,environments_json,location,assignment_group,
                    confidence,extraction_json,enriched_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(incident_number) DO UPDATE SET
                    row_hash=excluded.row_hash,
                    model=excluded.model,
                    source_file=excluded.source_file,
                    pattern_key=excluded.pattern_key,
                    pattern_label=excluded.pattern_label,
                    symptom=excluded.symptom,
                    resolution=excluded.resolution,
                    resolution_pattern=excluded.resolution_pattern,
                    root_cause=excluded.root_cause,
                    root_cause_pattern=excluded.root_cause_pattern,
                    technologies_json=excluded.technologies_json,
                    environments_json=excluded.environments_json,
                    location=excluded.location,
                    assignment_group=excluded.assignment_group,
                    confidence=excluded.confidence,
                    extraction_json=excluded.extraction_json,
                    enriched_at=excluded.enriched_at
                """,
                (
                    incident.number,
                    candidate.row_hash,
                    model_name,
                    candidate.source_file,
                    pattern_key,
                    pattern_label,
                    _normalise_space(extraction.symptom),
                    _normalise_space(extraction.resolution),
                    resolution_pattern,
                    _normalise_space(extraction.root_cause),
                    root_cause_pattern,
                    json.dumps(list(technologies), ensure_ascii=False),
                    json.dumps(list(environments), ensure_ascii=False),
                    incident.location or "",
                    incident.assignment_group or "",
                    float(extraction.confidence),
                    extraction.model_dump_json(),
                    _now_iso(),
                ),
            )
            conn.execute(
                "DELETE FROM support_knowledge_actions WHERE incident_number=?",
                (incident.number,),
            )
            for ordinal, action in enumerate(actions):
                if not _normalise_space(action.action):
                    continue
                conn.execute(
                    """
                    INSERT INTO support_knowledge_actions(
                        incident_number,ordinal,action,canonical_action,outcome,contributor,confidence
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        incident.number,
                        ordinal,
                        _normalise_space(action.action),
                        _canonical_action(action),
                        action.outcome,
                        _normalise_space(action.contributor),
                        float(action.confidence),
                    ),
                )

        return EnrichedIncidentKnowledge(
            incident_number=incident.number,
            row_hash=candidate.row_hash,
            model=model_name,
            source_file=candidate.source_file,
            pattern_key=pattern_key,
            pattern_label=pattern_label,
            symptom=_normalise_space(extraction.symptom),
            resolution=_normalise_space(extraction.resolution),
            resolution_pattern=resolution_pattern,
            root_cause=_normalise_space(extraction.root_cause),
            root_cause_pattern=root_cause_pattern,
            technologies=technologies,
            environments=environments,
            location=incident.location or "",
            assignment_group=incident.assignment_group or "",
            confidence=float(extraction.confidence),
            extraction=extraction,
            actions=actions,
        )

    def get(self, incident_number: str) -> EnrichedIncidentKnowledge | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM support_incident_knowledge WHERE UPPER(incident_number)=?",
                (str(incident_number or "").upper(),),
            ).fetchone()
            if row is None:
                return None
            action_rows = conn.execute(
                """
                SELECT * FROM support_knowledge_actions
                WHERE UPPER(incident_number)=?
                ORDER BY ordinal
                """,
                (str(incident_number or "").upper(),),
            ).fetchall()

        try:
            extraction = SupportExtraction.model_validate_json(row["extraction_json"])
        except Exception:
            return None
        actions = tuple(
            ExtractedAction(
                action=str(item["action"] or ""),
                canonical_action=str(item["canonical_action"] or ""),
                outcome=str(item["outcome"] or "unknown"),
                contributor=str(item["contributor"] or ""),
                confidence=float(item["confidence"] or 0.0),
            )
            for item in action_rows
        )
        return EnrichedIncidentKnowledge(
            incident_number=str(row["incident_number"]),
            row_hash=str(row["row_hash"]),
            model=str(row["model"]),
            source_file=str(row["source_file"] or ""),
            pattern_key=str(row["pattern_key"] or ""),
            pattern_label=str(row["pattern_label"] or ""),
            symptom=str(row["symptom"] or ""),
            resolution=str(row["resolution"] or ""),
            resolution_pattern=str(row["resolution_pattern"] or ""),
            root_cause=str(row["root_cause"] or ""),
            root_cause_pattern=str(row["root_cause_pattern"] or ""),
            technologies=tuple(_json_list(row["technologies_json"])),
            environments=tuple(_json_list(row["environments_json"])),
            location=str(row["location"] or ""),
            assignment_group=str(row["assignment_group"] or ""),
            confidence=float(row["confidence"] or 0.0),
            extraction=extraction,
            actions=actions,
        )

    def get_many(self, numbers: Iterable[str]) -> list[EnrichedIncidentKnowledge]:
        results: list[EnrichedIncidentKnowledge] = []
        for number in numbers:
            item = self.get(number)
            if item is not None:
                results.append(item)
        return results

    @staticmethod
    def _counter_rows(
        mapping: dict[str, set[str]],
        *,
        labels: dict[str, str] | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        labels = labels or {}
        ordered = sorted(mapping.items(), key=lambda item: (-len(item[1]), item[0]))
        rows = []
        for key, incidents in ordered[:limit]:
            rows.append(
                {
                    "label": labels.get(key, key),
                    "count": len(incidents),
                    "incidents": sorted(incidents)[:12],
                }
            )
        return rows

    def rebuild_patterns(self) -> int:
        """Materialise reusable issue-pattern rollups with incident provenance."""
        with self._connect() as conn:
            knowledge_rows = conn.execute(
                """
                SELECT * FROM support_incident_knowledge
                WHERE COALESCE(pattern_key, '') <> ''
                ORDER BY incident_number
                """
            ).fetchall()
            action_rows = conn.execute(
                "SELECT * FROM support_knowledge_actions ORDER BY incident_number, ordinal"
            ).fetchall()

        actions_by_incident: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in action_rows:
            actions_by_incident[str(row["incident_number"])].append(row)

        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in knowledge_rows:
            grouped[str(row["pattern_key"])].append(row)

        pattern_payloads: list[tuple] = []
        for pattern_key, rows in grouped.items():
            label_counts = Counter(str(row["pattern_label"] or "") for row in rows)
            label = next((name for name, _ in label_counts.most_common() if name), pattern_key)
            incidents = {str(row["incident_number"]) for row in rows}
            resolved = {
                str(row["incident_number"])
                for row in rows
                if str(row["resolution_pattern"] or row["resolution"] or "").strip()
            }
            confidences = [float(row["confidence"] or 0.0) for row in rows]

            resolution_map: dict[str, set[str]] = defaultdict(set)
            resolution_labels: dict[str, str] = {}
            root_map: dict[str, set[str]] = defaultdict(set)
            root_labels: dict[str, str] = {}
            tech_map: dict[str, set[str]] = defaultdict(set)
            env_map: dict[str, set[str]] = defaultdict(set)
            location_map: dict[str, set[str]] = defaultdict(set)
            successful_actions: dict[str, set[str]] = defaultdict(set)
            successful_action_labels: dict[str, str] = {}
            failed_actions: dict[str, set[str]] = defaultdict(set)
            failed_action_labels: dict[str, str] = {}

            for row in rows:
                number = str(row["incident_number"])
                resolution = str(row["resolution_pattern"] or row["resolution"] or "").strip()
                resolution_key = _normalise_label(resolution)
                if resolution_key:
                    resolution_map[resolution_key].add(number)
                    resolution_labels.setdefault(resolution_key, resolution)
                root = str(row["root_cause_pattern"] or row["root_cause"] or "").strip()
                root_key = _normalise_label(root)
                if root_key:
                    root_map[root_key].add(number)
                    root_labels.setdefault(root_key, root)
                for technology in _json_list(row["technologies_json"]):
                    value = _normalise_space(technology)
                    if value:
                        tech_map[_normalise_label(value)].add(number)
                for environment in _json_list(row["environments_json"]):
                    value = _normalise_space(environment)
                    if value:
                        env_map[_normalise_label(value)].add(number)
                location = _normalise_space(row["location"])
                if location:
                    location_map[_normalise_label(location)].add(number)

                for action in actions_by_incident.get(number, []):
                    label_value = str(action["canonical_action"] or action["action"] or "").strip()
                    key = _normalise_label(label_value)
                    if not key:
                        continue
                    outcome = str(action["outcome"] or "unknown").lower()
                    if outcome == "failed":
                        failed_actions[key].add(number)
                        failed_action_labels.setdefault(key, label_value)
                    elif outcome == "successful":
                        successful_actions[key].add(number)
                        successful_action_labels.setdefault(key, label_value)

            pattern_payloads.append(
                (
                    pattern_key,
                    label,
                    len(incidents),
                    len(resolved),
                    sum(confidences) / len(confidences) if confidences else 0.0,
                    json.dumps(self._counter_rows(resolution_map, labels=resolution_labels), ensure_ascii=False),
                    json.dumps(
                        self._counter_rows(successful_actions, labels=successful_action_labels),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        self._counter_rows(failed_actions, labels=failed_action_labels),
                        ensure_ascii=False,
                    ),
                    json.dumps(self._counter_rows(root_map, labels=root_labels), ensure_ascii=False),
                    json.dumps(self._counter_rows(tech_map), ensure_ascii=False),
                    json.dumps(self._counter_rows(env_map), ensure_ascii=False),
                    json.dumps(self._counter_rows(location_map), ensure_ascii=False),
                    json.dumps(sorted(incidents), ensure_ascii=False),
                    _now_iso(),
                )
            )

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM support_patterns")
            conn.executemany(
                """
                INSERT INTO support_patterns(
                    pattern_key,label,incident_count,resolved_count,avg_confidence,
                    resolution_counts_json,successful_action_counts_json,
                    failed_action_counts_json,root_cause_counts_json,technologies_json,
                    environments_json,locations_json,incident_numbers_json,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                pattern_payloads,
            )
        return len(pattern_payloads)

    def get_pattern(self, pattern_key: str) -> dict[str, Any] | None:
        if not pattern_key:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM support_patterns WHERE pattern_key=?",
                (pattern_key,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        for key in (
            "resolution_counts_json",
            "successful_action_counts_json",
            "failed_action_counts_json",
            "root_cause_counts_json",
            "technologies_json",
            "environments_json",
            "locations_json",
            "incident_numbers_json",
        ):
            item[key.removesuffix("_json")] = _json_list(item.get(key))
        return item


class OrganisationalKnowledgeIndex:
    """BGE-backed locator for enriched support knowledge, never the source of truth."""

    def __init__(self, vector_store: VectorStore | None = None):
        self.vector_store = vector_store or VectorStore(
            collection_name=KNOWLEDGE_COLLECTION,
            embedding_purpose="incident",
        )

    @staticmethod
    def _documents(item: EnrichedIncidentKnowledge) -> list[tuple[str, str, dict[str, Any]]]:
        metadata = {
            "number": item.incident_number,
            "pattern_key": item.pattern_key,
            "pattern_label": item.pattern_label,
            "confidence": float(item.confidence),
            "location": item.location,
            "assignment_group": item.assignment_group,
            "source": f"incident:{item.incident_number}",
        }
        docs: list[tuple[str, str, dict[str, Any]]] = []
        symptom_parts = [
            value
            for value in (
                f"Issue pattern: {item.pattern_label}" if item.pattern_label else "",
                f"Symptom: {item.symptom}" if item.symptom else "",
                f"Technologies: {', '.join(item.technologies)}" if item.technologies else "",
                f"Environment: {', '.join(item.environments)}" if item.environments else "",
                f"Location: {item.location}" if item.location else "",
            )
            if value
        ]
        if symptom_parts:
            docs.append(
                (
                    f"support-{item.incident_number}-symptom",
                    "\n".join(symptom_parts),
                    {**metadata, "knowledge_kind": "symptom"},
                )
            )

        resolution_parts = [
            value
            for value in (
                f"Issue pattern: {item.pattern_label}" if item.pattern_label else "",
                f"Resolution pattern: {item.resolution_pattern}" if item.resolution_pattern else "",
                f"Resolution: {item.resolution}" if item.resolution else "",
                f"Root cause: {item.root_cause}" if item.root_cause else "",
            )
            if value
        ]
        if resolution_parts:
            docs.append(
                (
                    f"support-{item.incident_number}-resolution",
                    "\n".join(resolution_parts),
                    {**metadata, "knowledge_kind": "resolution"},
                )
            )

        action_lines = []
        for action in item.actions:
            canonical = _canonical_action(action)
            if not canonical:
                continue
            action_lines.append(f"{action.outcome}: {canonical}")
        if action_lines:
            docs.append(
                (
                    f"support-{item.incident_number}-actions",
                    "\n".join(action_lines),
                    {**metadata, "knowledge_kind": "actions"},
                )
            )
        return docs

    def upsert_many(self, items: Iterable[EnrichedIncidentKnowledge]) -> dict[str, int]:
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        ids: list[str] = []
        incidents = 0
        for item in items:
            incidents += 1
            stale_ids = [
                f"support-{item.incident_number}-symptom",
                f"support-{item.incident_number}-resolution",
                f"support-{item.incident_number}-actions",
            ]
            self.vector_store.delete_documents(stale_ids)
            for doc_id, text, metadata in self._documents(item):
                ids.append(doc_id)
                documents.append(text)
                metadatas.append(metadata)
        if documents:
            self.vector_store.add_documents(
                documents,
                metadatas=metadatas,
                ids=ids,
                batch_size=settings.incident_embedding_batch_size,
            )
        return {
            "incidents": incidents,
            "documents": len(documents),
            "collection_total": self.vector_store.count(),
        }


class OrganisationalKnowledgeRetriever:
    """Render exact enriched knowledge and deterministic cross-incident insights."""

    def __init__(
        self,
        store: OrganisationalKnowledgeStore | None = None,
        index: OrganisationalKnowledgeIndex | None = None,
    ):
        self.store = store or OrganisationalKnowledgeStore()
        self.index = index or OrganisationalKnowledgeIndex()

    @staticmethod
    def _render_counter(title: str, rows: list[dict[str, Any]], *, limit: int = 5) -> list[str]:
        if not rows:
            return []
        lines = [title]
        for row in rows[:limit]:
            incidents = ", ".join(row.get("incidents") or [])
            suffix = f" — {incidents}" if incidents else ""
            lines.append(f"- {row.get('label')}: {row.get('count')} incident(s){suffix}")
        return lines

    def exact_context(self, incident_number: str, *, max_chars: int = 8000) -> str:
        item = self.store.get(incident_number)
        if item is None:
            return ""
        lines = [
            f"### Enriched support knowledge — {item.incident_number}",
            "This section is structured knowledge extracted from the incident evidence; every claim remains attributable to the source incident.",
            f"Issue pattern: {item.pattern_label or '[not confidently identified]'}",
            f"Symptom: {item.symptom or '[not confidently identified]'}",
        ]
        if item.technologies:
            lines.append("Technologies: " + ", ".join(item.technologies))
        if item.environments:
            lines.append("Environment: " + ", ".join(item.environments))
        if item.root_cause:
            lines.append(f"Root cause: {item.root_cause}")
        elif item.root_cause_pattern:
            lines.append(f"Root-cause pattern: {item.root_cause_pattern}")

        if item.actions:
            lines.extend(["", "#### Troubleshooting sequence"])
            for index, action in enumerate(item.actions, 1):
                canonical = _canonical_action(action)
                detail = _normalise_space(action.action)
                label = canonical or detail
                if detail and canonical and _normalise_label(detail) != _normalise_label(canonical):
                    label += f" — {detail}"
                lines.append(
                    f"{index}. [{action.outcome}] {label} (evidence confidence {float(action.confidence):.2f})"
                )

        lines.extend(
            [
                "",
                "#### Resolution knowledge",
                f"Resolution pattern: {item.resolution_pattern or '[not confidently identified]'}",
                f"Recorded/enriched resolution: {item.resolution or '[not confidently identified]'}",
                f"Overall extraction confidence: {item.confidence:.2f}",
            ]
        )

        pattern = self.store.get_pattern(item.pattern_key)
        if pattern and int(pattern.get("incident_count") or 0) > 1:
            lines.extend(
                [
                    "",
                    f"#### Organisational pattern — {pattern.get('label')}",
                    f"Known enriched incidents in this pattern: {pattern.get('incident_count')}",
                    f"Incidents with recorded resolution knowledge: {pattern.get('resolved_count')}",
                ]
            )
            lines.extend(self._render_counter("Repeated resolutions:", pattern.get("resolution_counts") or []))
            lines.extend(self._render_counter("Repeated successful actions:", pattern.get("successful_action_counts") or []))
            lines.extend(self._render_counter("Repeated failed actions:", pattern.get("failed_action_counts") or []))
            lines.extend(self._render_counter("Observed root causes:", pattern.get("root_cause_counts") or []))

        text = "\n".join(lines).strip()
        return text if len(text) <= max_chars else text[:max_chars].rstrip() + "\n[Enriched knowledge truncated]"

    @staticmethod
    def _aggregate_selected(items: list[tuple[EnrichedIncidentKnowledge, float]]) -> dict[str, Any]:
        resolution_map: dict[str, set[str]] = defaultdict(set)
        resolution_labels: dict[str, str] = {}
        successful_map: dict[str, set[str]] = defaultdict(set)
        successful_labels: dict[str, str] = {}
        failed_map: dict[str, set[str]] = defaultdict(set)
        failed_labels: dict[str, str] = {}
        root_map: dict[str, set[str]] = defaultdict(set)
        root_labels: dict[str, str] = {}
        tech_map: dict[str, set[str]] = defaultdict(set)
        location_map: dict[str, set[str]] = defaultdict(set)
        pattern_map: dict[str, set[str]] = defaultdict(set)
        pattern_labels: dict[str, str] = {}

        for item, _score in items:
            number = item.incident_number
            if item.pattern_key:
                pattern_map[item.pattern_key].add(number)
                pattern_labels.setdefault(item.pattern_key, item.pattern_label or item.pattern_key)
            resolution = item.resolution_pattern or item.resolution
            key = _normalise_label(resolution)
            if key:
                resolution_map[key].add(number)
                resolution_labels.setdefault(key, resolution)
            root = item.root_cause_pattern or item.root_cause
            key = _normalise_label(root)
            if key:
                root_map[key].add(number)
                root_labels.setdefault(key, root)
            for technology in item.technologies:
                key = _normalise_label(technology)
                if key:
                    tech_map[key].add(number)
            if item.location:
                location_map[_normalise_label(item.location)].add(number)
            for action in item.actions:
                label = _canonical_action(action)
                key = _normalise_label(label)
                if not key:
                    continue
                if action.outcome == "failed":
                    failed_map[key].add(number)
                    failed_labels.setdefault(key, label)
                elif action.outcome == "successful":
                    successful_map[key].add(number)
                    successful_labels.setdefault(key, label)

        counter = OrganisationalKnowledgeStore._counter_rows
        return {
            "patterns": counter(pattern_map, labels=pattern_labels),
            "resolutions": counter(resolution_map, labels=resolution_labels),
            "successful_actions": counter(successful_map, labels=successful_labels),
            "failed_actions": counter(failed_map, labels=failed_labels),
            "root_causes": counter(root_map, labels=root_labels),
            "technologies": counter(tech_map),
            "locations": counter(location_map),
        }

    def collective_context(
        self,
        query: str,
        *,
        candidate_k: int = 40,
        max_incidents: int = 24,
        min_similarity: float | None = None,
        max_chars: int = 9000,
    ) -> str:
        """Find similar *symptoms*, then aggregate complete enriched case knowledge."""
        query = _normalise_space(query)
        if not query:
            return ""
        threshold = (
            settings.knowledge_min_similarity
            if min_similarity is None
            else float(min_similarity)
        )
        try:
            docs = self.index.vector_store.similarity_search(
                query,
                k=max(1, int(candidate_k)),
                where={"knowledge_kind": "symptom"},
            )
        except Exception:
            return ""

        best_scores: dict[str, float] = {}
        for doc in docs:
            meta = doc.metadata or {}
            number = str(meta.get("number") or "").upper().strip()
            score = float(meta.get("score") or 0.0)
            if not number or score < threshold:
                continue
            if score > best_scores.get(number, -1.0):
                best_scores[number] = score

        ranked = sorted(best_scores.items(), key=lambda item: item[1], reverse=True)[:max_incidents]
        selected: list[tuple[EnrichedIncidentKnowledge, float]] = []
        for number, score in ranked:
            item = self.store.get(number)
            if item is not None:
                selected.append((item, score))
        if not selected:
            return ""

        aggregate = self._aggregate_selected(selected)
        incident_numbers = [item.incident_number for item, _ in selected]
        mean_confidence = sum(item.confidence for item, _ in selected) / len(selected)
        mean_similarity = sum(score for _, score in selected) / len(selected)
        if len(selected) >= 8 and mean_confidence >= 0.70:
            quality = "strong"
        elif len(selected) >= 3 and mean_confidence >= 0.50:
            quality = "moderate"
        else:
            quality = "limited"

        lines = [
            "### Organisational pattern evidence",
            "The following counts are deterministic rollups over symptomatically similar enriched incidents; they are not LLM-estimated probabilities.",
            f"Retrieved enriched incidents: {len(selected)}",
            f"Mean semantic symptom similarity: {mean_similarity:.2f}",
            f"Mean extraction confidence: {mean_confidence:.2f}",
            f"Evidence strength: {quality}",
            "Supporting incidents: " + ", ".join(incident_numbers[:20]),
        ]
        lines.extend(self._render_counter("Dominant issue patterns:", aggregate["patterns"]))
        lines.extend(self._render_counter("Observed resolutions:", aggregate["resolutions"]))
        lines.extend(self._render_counter("Observed successful actions:", aggregate["successful_actions"]))
        lines.extend(self._render_counter("Observed failed actions / no-fix paths:", aggregate["failed_actions"]))
        lines.extend(self._render_counter("Observed root causes:", aggregate["root_causes"]))
        lines.extend(self._render_counter("Common technologies:", aggregate["technologies"], limit=4))
        lines.extend(self._render_counter("Common locations:", aggregate["locations"], limit=4))
        lines.extend(
            [
                "",
                "#### Closest enriched cases",
            ]
        )
        for item, score in selected[:5]:
            lines.append(
                f"- {item.incident_number} (similarity {score:.2f}) — "
                f"symptom: {item.symptom or item.pattern_label or '[not captured]'}; "
                f"resolution: {item.resolution_pattern or item.resolution or '[not captured]'}"
            )

        text = "\n".join(lines).strip()
        return text if len(text) <= max_chars else text[:max_chars].rstrip() + "\n[Organisational pattern context truncated]"

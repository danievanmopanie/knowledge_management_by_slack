"""Deterministic incident case-file retrieval for Frontend Support.

Vector search is useful for *finding* similar incidents, but once an incident
number is known we should read the authoritative structured state directly.
This module hydrates the full current record, temporal versions/transitions,
assignment dwell history and graph relationships without any embedding or LLM
lookup.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.config import settings
from src.core.database import connect
from src.knowledge.graphstore import GraphStore

INCIDENT_NUMBER_RE = re.compile(r"\bINC\d{4,}\b", re.IGNORECASE)


def extract_incident_numbers(text: str) -> list[str]:
    """Return unique ServiceNow incident numbers in first-seen order."""
    seen: set[str] = set()
    numbers: list[str] = []
    for match in INCIDENT_NUMBER_RE.finditer(text or ""):
        number = match.group(0).upper()
        if number in seen:
            continue
        seen.add(number)
        numbers.append(number)
    return numbers


def looks_like_exact_incident_lookup(text: str) -> bool:
    """Detect requests whose primary intent is to inspect a named incident.

    The Frontend Support thread query contains contextual boilerplate, so this
    deliberately looks for explicit lookup/history/resolution language rather
    than treating every bound INC number as an exact-lookup request.
    """
    if not extract_incident_numbers(text):
        return False
    lowered = " ".join((text or "").lower().split())
    cues = (
        "what happened",
        "how it was resolved",
        "how was it resolved",
        "how was this resolved",
        "how it got resolved",
        "check what happened",
        "check incident",
        "look up",
        "lookup",
        "incident history",
        "ticket history",
        "tell me about inc",
        "details for inc",
        "resolution for inc",
        "resolved inc",
    )
    return any(cue in lowered for cue in cues)


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …"


@dataclass
class IncidentCaseFile:
    number: str
    current: dict[str, Any]
    versions: list[dict[str, Any]] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    dwell: list[dict[str, Any]] = field(default_factory=list)
    graph_relations: list[dict[str, Any]] = field(default_factory=list)
    latest_seen_at: str = ""
    source_file: str = ""
    is_missing: bool = False


class IncidentCaseFileStore:
    """Read complete incident evidence directly from the temporal/graph stores."""

    def __init__(
        self,
        path: Path | None = None,
        graph_store: GraphStore | None = None,
    ):
        self.path = Path(path or settings.platform_db_path)
        self.graph_store = graph_store or GraphStore()

    @staticmethod
    def _decode_record(value: str | None) -> dict[str, Any]:
        try:
            return json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}

    def get(self, number: str) -> IncidentCaseFile | None:
        number = str(number or "").strip().upper()
        if not INCIDENT_NUMBER_RE.fullmatch(number):
            return None
        try:
            with connect(self.path) as conn:
                current_row = conn.execute(
                    "SELECT * FROM incident_current WHERE UPPER(number)=?",
                    (number,),
                ).fetchone()
                if current_row is None:
                    return None

                versions = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM incident_versions WHERE UPPER(number)=? ORDER BY id",
                        (number,),
                    ).fetchall()
                ]
                transitions = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM incident_transitions WHERE UPPER(number)=? ORDER BY id",
                        (number,),
                    ).fetchall()
                ]
                dwell = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM assignment_dwell WHERE UPPER(number)=? ORDER BY id",
                        (number,),
                    ).fetchall()
                ]
        except sqlite3.OperationalError:
            # A fresh test/dev database may not have the temporal schema yet.
            return None

        for version in versions:
            version["record"] = self._decode_record(version.get("record_json"))

        graph_relations = self.graph_store.get_related(f"incident:{number}", max_depth=1)
        row = dict(current_row)
        return IncidentCaseFile(
            number=number,
            current=self._decode_record(row.get("record_json")),
            versions=versions,
            transitions=transitions,
            dwell=dwell,
            graph_relations=graph_relations,
            latest_seen_at=str(row.get("latest_seen_at") or ""),
            source_file=str(row.get("source_file") or ""),
            is_missing=bool(row.get("is_missing") or 0),
        )

    @staticmethod
    def _identity_line(record: dict[str, Any]) -> str:
        fields = [
            ("State", record.get("state")),
            ("Priority", record.get("priority")),
            ("Assignment group", record.get("assignment_group")),
            ("Assigned to", record.get("assigned_to")),
            ("Location", record.get("location")),
            ("CI", record.get("configuration_item")),
            ("Category", record.get("category")),
            ("Subcategory", record.get("subcategory")),
        ]
        return " | ".join(f"{label}: {value}" for label, value in fields if str(value or "").strip())

    def render_exact(self, case: IncidentCaseFile, *, max_chars: int = 14000) -> str:
        """Render a rich evidence packet for an explicit incident lookup."""
        r = case.current
        lines = [
            f"### Exact incident case file — {case.number}",
            "Source: deterministic temporal incident store (exact lookup; not semantic similarity)",
        ]
        identity = self._identity_line(r)
        if identity:
            lines.append(identity)
        lines.extend(
            [
                f"Opened: {r.get('opened_at') or 'not captured'} | Updated: {r.get('updated_at') or 'not captured'} | Resolved: {r.get('resolved_at') or 'not captured'} | Closed: {r.get('closed_at') or 'not captured'}",
                f"Latest snapshot seen: {case.latest_seen_at or 'not captured'} | Source file: {case.source_file or 'not captured'} | Missing from latest complete snapshot: {'yes' if case.is_missing else 'no'}",
                "",
                "#### What was reported",
                f"Short description: {_clip(r.get('short_description'), 1200) or '[not captured]'}",
                f"Description: {_clip(r.get('description'), 3200) or '[not captured]'}",
                "",
                "#### Technician / customer evidence",
                f"Work notes: {_clip(r.get('work_notes'), 5200) or '[not captured]'}",
                f"Comments: {_clip(r.get('comments'), 2200) or '[not captured]'}",
                "",
                "#### Recorded resolution",
                f"Resolution notes: {_clip(r.get('resolution_notes'), 4200) or '[no resolution notes captured in the imported incident record]'}",
            ]
        )

        if len(case.versions) > 1 or case.transitions or case.dwell:
            lines.extend(["", "#### Temporal history"])
            for version in case.versions[-8:]:
                vr = version.get("record") or {}
                lines.append(
                    "- Version observed "
                    f"{version.get('observed_at') or '?'}: state={vr.get('state') or '?'}; "
                    f"group={vr.get('assignment_group') or '?'}; assigned_to={vr.get('assigned_to') or '?'}; "
                    f"record_updated={vr.get('updated_at') or '?'}; source={version.get('source_file') or '?'}"
                )
            for transition in case.transitions[-20:]:
                lines.append(
                    f"- Transition {transition.get('field')}: "
                    f"{transition.get('from_value') or '[blank]'} → {transition.get('to_value') or '[blank]'} "
                    f"at {transition.get('event_time') or transition.get('observed_at') or '?'}"
                )
            for item in case.dwell[-12:]:
                lines.append(
                    f"- Assignment dwell: {item.get('assignment_group') or '?'} for "
                    f"{item.get('dwell_seconds') if item.get('dwell_seconds') is not None else '?'} seconds; "
                    f"next={item.get('next_group') or '[resolved/no handoff]'}; "
                    f"state_at_exit={item.get('state_at_exit') or '?'}"
                )

        if case.graph_relations:
            lines.extend(["", "#### Structured graph relationships"])
            for rel in case.graph_relations[:24]:
                edge = rel.get("edge_properties") or {}
                props = rel.get("properties") or {}
                name = props.get("name") or rel.get("entity") or "?"
                validity = ""
                if edge.get("valid_from"):
                    validity = f" | valid_from={edge.get('valid_from')}"
                if edge.get("valid_to"):
                    validity += f" | valid_to={edge.get('valid_to')}"
                confidence = ""
                if edge.get("confidence") is not None:
                    confidence = f" | confidence={edge.get('confidence')}"
                lines.append(
                    f"- {rel.get('relation') or 'related_to'} → {name} [{rel.get('type') or 'entity'}]"
                    f"{validity}{confidence}"
                )

        text = "\n".join(lines).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n\n[Exact incident evidence truncated]"
        return text

    def render_similar(
        self,
        case: IncidentCaseFile,
        *,
        relevance: float | None = None,
        matched_fields: str = "",
        max_chars: int = 3200,
    ) -> str:
        """Render the complete useful parts of a semantically-selected incident."""
        r = case.current
        header = f"{case.number}"
        if relevance is not None:
            header += f" | semantic relevance={relevance:.2f}"
        if matched_fields:
            header += f" | matched={matched_fields}"
        identity = self._identity_line(r)
        lines = [f"#### {header}"]
        if identity:
            lines.append(identity)
        lines.extend(
            [
                f"Problem: {_clip(r.get('short_description'), 600) or '[not captured]'}",
                f"Description: {_clip(r.get('description'), 1000) or '[not captured]'}",
                f"Work notes: {_clip(r.get('work_notes'), 1400) or '[not captured]'}",
                f"Resolution notes: {_clip(r.get('resolution_notes'), 1200) or '[no resolution notes captured]'}",
            ]
        )
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n[Case evidence truncated]"
        return text

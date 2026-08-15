"""Deterministic temporal graph construction for ServiceNow incidents.

Structured ServiceNow fields are authoritative.  This module intentionally does
not call an LLM; free-text is used only for cheap ticket-reference extraction.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from src.knowledge.graphstore import GraphStore
from src.reporting.incidents import Incident

_TICKET_PATTERNS = {
    "change": re.compile(r"\bCHG\d+\b", re.I),
    "problem": re.compile(r"\bPRB\d+\b", re.I),
    "knowledge_item": re.compile(r"\bKB\d+\b", re.I),
}


def _iso(value: datetime | None, fallback: str) -> str:
    if not value:
        return fallback
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", (value or "").strip().lower()).strip("-") or "unknown"


def _raw(inc: Incident, *names: str) -> str:
    wanted = {re.sub(r"\s+", " ", n.replace("_", " ").strip().lower()) for n in names}
    for key, value in (inc.raw or {}).items():
        norm = re.sub(r"\s+", " ", str(key).replace("_", " ").strip().lower())
        if norm in wanted and str(value or "").strip():
            return str(value).strip()
    return ""


def _change_relation(text: str, change_number: str) -> tuple[str, float, str]:
    """Grade a CHG reference conservatively without an LLM call."""
    lowered = text.lower()
    chg = re.escape(change_number.lower())
    if re.search(rf"(caused by|root cause(?: was| is)?|because of|due to)\s+{chg}", lowered):
        return "confirmed_caused", 0.90, "explicit_causation_text"
    if re.search(rf"(rollback|backed out|failed change|deployment failed).*{chg}|{chg}.*(rollback|backed out|failed change|deployment failed)", lowered):
        return "likely_caused", 0.70, "failure_or_rollback_language"
    if re.search(rf"(after|following|post)\s+{chg}|{chg}.*(after|following|post)", lowered):
        return "temporally_correlated_with", 0.55, "temporal_language"
    return "mentions_change", 0.25, "text_reference"


@dataclass
class GraphBuildResult:
    incidents: int
    nodes_added: int
    edges_added: int
    temporal_edges_opened: int
    reference_edges: int
    elapsed_seconds: float


class FastTemporalGraphBuilder:
    """Build current + historical structured relationships in one in-memory batch."""

    def __init__(self, store: GraphStore | None = None):
        self.store = store or GraphStore()

    def _entity(self, entity_type: str, value: str) -> str:
        entity_id = f"{entity_type}:{_slug(value)}"
        self.store.add_entity(entity_id, entity_type=entity_type, name=value.strip())
        return entity_id

    def build(
        self,
        incidents: Iterable[Incident],
        *,
        observed_at: str,
        source_file: str,
        save: bool = True,
    ) -> GraphBuildResult:
        started = time.perf_counter()
        before_nodes = self.store.graph.number_of_nodes()
        before_edges = self.store.graph.number_of_edges()
        temporal_opened = 0
        reference_edges = 0
        count = 0

        for inc in incidents:
            if not inc.number:
                continue
            count += 1
            incident_id = f"incident:{inc.number}"
            event_time = _iso(inc.updated_at or inc.opened_at, observed_at)
            opened_time = _iso(inc.opened_at, observed_at)
            self.store.add_entity(
                incident_id,
                entity_type="incident",
                name=inc.number,
                short_description=inc.short_description or "",
                opened_at=_iso(inc.opened_at, ""),
                resolved_at=_iso(inc.resolved_at, ""),
                state=inc.state or "",
                source_file=source_file,
                last_observed_at=observed_at,
            )

            structured = [
                (inc.state, "state", "in_state"),
                (inc.priority, "priority", "has_priority"),
                (inc.assignment_group, "assignment_group", "assigned_to_group"),
                (inc.assigned_to, "person", "assigned_to"),
                (inc.location, "location", "located_at"),
                (inc.category, "category", "categorised_as"),
                (inc.subcategory, "subcategory", "subcategorised_as"),
                (inc.configuration_item, "configuration_item", "affects"),
            ]
            for value, entity_type, relation in structured:
                if not value:
                    continue
                target = self._entity(entity_type, value)
                if self.store.set_temporal_relation(
                    incident_id,
                    target,
                    relation,
                    valid_from=event_time,
                    observed_at=observed_at,
                    source_file=source_file,
                ):
                    temporal_opened += 1

            if inc.caller:
                reporter = self._entity("person", inc.caller)
                self.store.add_relation(
                    incident_id,
                    reporter,
                    relation="reported_by",
                    valid_from=opened_time,
                    observed_at=observed_at,
                    source_file=source_file,
                )

            text = " ".join(
                part
                for part in (
                    inc.short_description,
                    inc.description,
                    inc.work_notes,
                    inc.comments,
                    inc.resolution_notes,
                )
                if part
            )
            for entity_type, pattern in _TICKET_PATTERNS.items():
                for ticket in sorted({m.group(0).upper() for m in pattern.finditer(text)}):
                    target = self._entity(entity_type, ticket)
                    if entity_type == "change":
                        relation, confidence, evidence = _change_relation(text, ticket)
                    else:
                        relation = {
                            "problem": "related_to_problem",
                            "knowledge_item": "references_knowledge",
                        }[entity_type]
                        confidence, evidence = 1.0, "text_reference"
                    self.store.add_relation(
                        incident_id,
                        target,
                        relation=relation,
                        valid_from=event_time,
                        observed_at=observed_at,
                        source_file=source_file,
                        confidence=confidence,
                        evidence=evidence,
                    )
                    reference_edges += 1

        if save:
            self.store.save(compact=True)

        return GraphBuildResult(
            incidents=count,
            nodes_added=self.store.graph.number_of_nodes() - before_nodes,
            edges_added=self.store.graph.number_of_edges() - before_edges,
            temporal_edges_opened=temporal_opened,
            reference_edges=reference_edges,
            elapsed_seconds=time.perf_counter() - started,
        )

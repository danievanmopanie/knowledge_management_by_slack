"""Seed the typed support graph from historical ServiceNow incident exports."""

from __future__ import annotations

from src.knowledge.support_graph import GraphEntity, SupportEntityType, SupportKnowledgeGraph, SupportRelation
from src.reporting.incidents import Incident


def seed_incident_graph(incident: Incident, graph: SupportKnowledgeGraph) -> GraphEntity | None:
    """Create deterministic graph facts from fields we can trust without LLM inference.

    Rich action/root-cause extraction is intentionally left to the extraction model.
    This seed step only records relationships directly supported by CSV fields.

    Facts are backdated to when the incident actually happened (resolved_at, falling
    back to opened_at) rather than import time, so recency-based retrieval reflects
    real support history instead of ranking every historical incident as "just now".
    """
    number = (incident.number or "").strip()
    if not number:
        return None

    occurred_at = incident.resolved_at or incident.opened_at
    occurred_at_iso = occurred_at.isoformat() if occurred_at else None

    node = graph.add_incident(
        number,
        short_description=incident.short_description or "",
        state=incident.state or "",
        assigned_to=incident.assigned_to or "",
        caller=incident.caller or "",
        occurred_at=occurred_at_iso,
    )

    symptom = (incident.short_description or incident.description or "").strip()
    if symptom:
        graph.add_symptom(node, symptom, confidence=1.0, occurred_at=occurred_at_iso)

    if incident.category:
        technology = GraphEntity(
            SupportEntityType.TECHNOLOGY,
            incident.category,
            incident.category,
            {"subcategory": incident.subcategory or ""},
        )
        graph.relate(node, technology, SupportRelation.AFFECTS, occurred_at=occurred_at_iso)

    if incident.location:
        environment = GraphEntity(
            SupportEntityType.ENVIRONMENT,
            incident.location,
            incident.location,
        )
        graph.relate(node, environment, SupportRelation.OCCURRED_IN, occurred_at=occurred_at_iso)

    resolution = (incident.resolution_notes or "").strip()
    if resolution and (incident.state or "").strip().lower() in {"resolved", "closed"}:
        graph.add_resolution(
            node,
            resolution,
            resolver=(incident.assigned_to or "").strip() or None,
            confidence=1.0,
            occurred_at=occurred_at_iso,
        )

    return node


def seed_incidents(incidents: list[Incident], graph: SupportKnowledgeGraph | None = None) -> dict[str, int]:
    support_graph = graph or SupportKnowledgeGraph()
    seeded = 0
    skipped = 0
    for incident in incidents:
        if seed_incident_graph(incident, support_graph) is None:
            skipped += 1
        else:
            seeded += 1
    support_graph.save()
    return {"seeded": seeded, "skipped": skipped}

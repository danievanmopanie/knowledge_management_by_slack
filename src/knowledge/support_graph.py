"""Typed support knowledge graph for incidents, patterns, actions and resolutions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.core.config import settings
from src.knowledge.graphstore import GraphStore


class SupportEntityType(StrEnum):
    INCIDENT = "incident"
    PERSON = "person"
    ISSUE_PATTERN = "issue_pattern"
    SYMPTOM = "symptom"
    ACTION = "action"
    RESOLUTION = "resolution"
    TECHNOLOGY = "technology"
    ENVIRONMENT = "environment"
    SLACK_THREAD = "slack_thread"
    KNOWLEDGE_ITEM = "knowledge_item"


class SupportRelation(StrEnum):
    EXEMPLIFIES = "exemplifies"
    HAS_SYMPTOM = "has_symptom"
    AFFECTS = "affects"
    OCCURRED_IN = "occurred_in"
    TRIED = "tried"
    CONTRIBUTED = "contributed"
    RESOLVED = "resolved"
    RESOLVED_BY = "resolved_by"
    SUCCESSFUL_FIX = "successful_fix"
    FAILED_ACTION = "failed_action"
    REPORTED_BY = "reported_by"
    DISCUSSED_IN = "discussed_in"
    REFERENCES = "references"
    PROMOTED_TO = "promoted_to"
    SIMILAR_TO = "similar_to"


def _slug(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip().lower())
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    return value.strip("-") or "unknown"


@dataclass(frozen=True)
class GraphEntity:
    entity_type: SupportEntityType
    key: str
    name: str
    properties: dict[str, Any] | None = None

    @property
    def entity_id(self) -> str:
        return f"{self.entity_type.value}:{_slug(self.key)}"


class SupportKnowledgeGraph:
    """Domain adapter over a graph dedicated to enriched support knowledge.

    The fast temporal ingest graph intentionally uses ``data/vectorstore/graph.json``.
    Rich LLM enrichment uses ``data/vectorstore/support_graph/graph.json`` so two
    background processes cannot overwrite each other's whole-file NetworkX state.
    SQLite remains the authoritative source for enriched facts and rollups.
    """

    def __init__(self, store: GraphStore | None = None):
        self.store = store or GraphStore(path=settings.vectorstore_path / "support_graph")

    def upsert(self, entity: GraphEntity) -> str:
        props = dict(entity.properties or {})
        self.store.add_entity(
            entity.entity_id,
            entity_type=entity.entity_type.value,
            name=entity.name,
            **props,
        )
        return entity.entity_id

    def relate(
        self,
        source: GraphEntity,
        target: GraphEntity,
        relation: SupportRelation,
        **properties: Any,
    ) -> None:
        source_id = self.upsert(source)
        target_id = self.upsert(target)
        self.store.add_relation(
            source_id,
            target_id,
            relation=relation.value,
            **properties,
        )

    def add_incident(
        self,
        number: str,
        *,
        short_description: str = "",
        state: str = "",
        assigned_to: str = "",
        caller: str = "",
    ) -> GraphEntity:
        incident = GraphEntity(
            SupportEntityType.INCIDENT,
            number,
            number,
            {
                "short_description": short_description,
                "state": state,
            },
        )
        self.upsert(incident)
        if assigned_to:
            person = GraphEntity(SupportEntityType.PERSON, assigned_to, assigned_to)
            relation = (
                SupportRelation.RESOLVED_BY
                if state.lower() in {"resolved", "closed"}
                else SupportRelation.CONTRIBUTED
            )
            self.relate(incident, person, relation)
        if caller:
            reporter = GraphEntity(SupportEntityType.PERSON, caller, caller)
            self.relate(incident, reporter, SupportRelation.REPORTED_BY)
        return incident

    def add_issue_pattern(
        self,
        incident: GraphEntity,
        pattern: str,
        *,
        confidence: float | None = None,
    ) -> GraphEntity:
        entity = GraphEntity(SupportEntityType.ISSUE_PATTERN, pattern, pattern)
        props: dict[str, Any] = {}
        if confidence is not None:
            props["confidence"] = confidence
        self.relate(incident, entity, SupportRelation.EXEMPLIFIES, **props)
        return entity

    def add_symptom(
        self,
        incident: GraphEntity,
        symptom: str,
        *,
        confidence: float | None = None,
    ) -> GraphEntity:
        entity = GraphEntity(SupportEntityType.SYMPTOM, symptom, symptom)
        props: dict[str, Any] = {}
        if confidence is not None:
            props["confidence"] = confidence
        self.relate(incident, entity, SupportRelation.HAS_SYMPTOM, **props)
        return entity

    def add_action(
        self,
        incident: GraphEntity,
        action: str,
        *,
        canonical_action: str = "",
        outcome: str = "unknown",
        contributor: str | None = None,
        confidence: float | None = None,
    ) -> GraphEntity:
        key = canonical_action.strip() or action
        properties: dict[str, Any] = {
            "evidence_text": action,
            "canonical_action": canonical_action.strip(),
        }
        if confidence is not None:
            properties["confidence"] = confidence
        entity = GraphEntity(SupportEntityType.ACTION, key, key, properties)
        edge_props: dict[str, Any] = {"outcome": outcome, "evidence_text": action}
        if confidence is not None:
            edge_props["confidence"] = confidence
        self.relate(incident, entity, SupportRelation.TRIED, **edge_props)
        if outcome.lower() in {"failed", "unsuccessful", "no_change"}:
            self.relate(incident, entity, SupportRelation.FAILED_ACTION, **edge_props)
        if contributor:
            person = GraphEntity(SupportEntityType.PERSON, contributor, contributor)
            self.relate(person, entity, SupportRelation.CONTRIBUTED)
        return entity

    def add_resolution(
        self,
        incident: GraphEntity,
        resolution: str,
        *,
        resolution_pattern: str = "",
        resolver: str | None = None,
        root_cause: str = "",
        root_cause_pattern: str = "",
        confidence: float | None = None,
    ) -> GraphEntity:
        key = resolution_pattern.strip() or f"{incident.key}:{resolution}"
        props: dict[str, Any] = {
            "evidence_text": resolution,
            "resolution_pattern": resolution_pattern.strip(),
            "root_cause": root_cause,
            "root_cause_pattern": root_cause_pattern.strip(),
        }
        if confidence is not None:
            props["confidence"] = confidence
        entity = GraphEntity(
            SupportEntityType.RESOLUTION,
            key,
            resolution_pattern.strip() or resolution,
            props,
        )
        self.relate(
            incident,
            entity,
            SupportRelation.SUCCESSFUL_FIX,
            evidence_text=resolution,
            confidence=confidence if confidence is not None else 0.0,
        )
        if resolver:
            person = GraphEntity(SupportEntityType.PERSON, resolver, resolver)
            self.relate(person, incident, SupportRelation.RESOLVED)
            self.relate(entity, person, SupportRelation.RESOLVED_BY)
        return entity

    def link_thread(self, incident: GraphEntity, channel_id: str, thread_ts: str) -> GraphEntity:
        key = f"{channel_id}:{thread_ts}"
        thread = GraphEntity(
            SupportEntityType.SLACK_THREAD,
            key,
            key,
            {"channel_id": channel_id, "thread_ts": thread_ts},
        )
        self.relate(incident, thread, SupportRelation.DISCUSSED_IN)
        self.relate(thread, incident, SupportRelation.REFERENCES)
        return thread

    def related(self, entity: GraphEntity | str, *, depth: int = 1) -> list[dict[str, Any]]:
        entity_id = entity if isinstance(entity, str) else entity.entity_id
        return self.store.get_related(entity_id, max_depth=depth)

    def save(self) -> None:
        self.store.save()

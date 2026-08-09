"""Typed support knowledge graph for incidents, people, symptoms, actions and resolutions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.knowledge.graphstore import GraphStore


class SupportEntityType(StrEnum):
    INCIDENT = "incident"
    PERSON = "person"
    SYMPTOM = "symptom"
    ACTION = "action"
    RESOLUTION = "resolution"
    TECHNOLOGY = "technology"
    ENVIRONMENT = "environment"
    SLACK_THREAD = "slack_thread"
    KNOWLEDGE_ITEM = "knowledge_item"


class SupportRelation(StrEnum):
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
    """Domain adapter over GraphStore for collaborative support knowledge."""

    def __init__(self, store: GraphStore | None = None):
        self.store = store or GraphStore()

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
            self.relate(incident, person, SupportRelation.RESOLVED_BY if state.lower() in {"resolved", "closed"} else SupportRelation.CONTRIBUTED)
        if caller:
            reporter = GraphEntity(SupportEntityType.PERSON, caller, caller)
            self.relate(incident, reporter, SupportRelation.REPORTED_BY)
        return incident

    def add_symptom(self, incident: GraphEntity, symptom: str, *, confidence: float | None = None) -> GraphEntity:
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
        outcome: str = "unknown",
        contributor: str | None = None,
    ) -> GraphEntity:
        entity = GraphEntity(SupportEntityType.ACTION, action, action)
        self.relate(incident, entity, SupportRelation.TRIED, outcome=outcome)
        if outcome.lower() in {"failed", "unsuccessful", "no_change"}:
            self.relate(incident, entity, SupportRelation.FAILED_ACTION)
        if contributor:
            person = GraphEntity(SupportEntityType.PERSON, contributor, contributor)
            self.relate(person, entity, SupportRelation.CONTRIBUTED)
        return entity

    def add_resolution(
        self,
        incident: GraphEntity,
        resolution: str,
        *,
        resolver: str | None = None,
        root_cause: str = "",
        confidence: float | None = None,
    ) -> GraphEntity:
        props: dict[str, Any] = {"root_cause": root_cause}
        if confidence is not None:
            props["confidence"] = confidence
        entity = GraphEntity(SupportEntityType.RESOLUTION, f"{incident.key}:{resolution}", resolution, props)
        self.relate(incident, entity, SupportRelation.SUCCESSFUL_FIX)
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

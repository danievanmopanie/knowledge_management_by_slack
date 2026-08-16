"""Typed support knowledge graph for incidents, people, symptoms, actions and resolutions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from src.knowledge.graphstore import GraphStore


def age_days(timestamp: str | None) -> int | None:
    """Return whole days since an ISO timestamp, or None when it cannot be parsed."""
    if not timestamp:
        return None
    try:
        then = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - then).days, 0)


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

    def upsert(self, entity: GraphEntity, *, occurred_at: str | None = None) -> str:
        props = dict(entity.properties or {})
        self.store.add_entity(
            entity.entity_id,
            entity_type=entity.entity_type.value,
            name=entity.name,
            occurred_at=occurred_at,
            **props,
        )
        return entity.entity_id

    def relate(
        self,
        source: GraphEntity,
        target: GraphEntity,
        relation: SupportRelation,
        *,
        occurred_at: str | None = None,
        **properties: Any,
    ) -> None:
        source_id = self.upsert(source, occurred_at=occurred_at)
        target_id = self.upsert(target, occurred_at=occurred_at)
        self.store.add_relation(
            source_id,
            target_id,
            relation=relation.value,
            occurred_at=occurred_at,
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
        occurred_at: str | None = None,
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
        self.upsert(incident, occurred_at=occurred_at)
        if assigned_to:
            person = GraphEntity(SupportEntityType.PERSON, assigned_to, assigned_to)
            self.relate(
                incident,
                person,
                SupportRelation.RESOLVED_BY if state.lower() in {"resolved", "closed"} else SupportRelation.CONTRIBUTED,
                occurred_at=occurred_at,
            )
        if caller:
            reporter = GraphEntity(SupportEntityType.PERSON, caller, caller)
            self.relate(incident, reporter, SupportRelation.REPORTED_BY, occurred_at=occurred_at)
        return incident

    def add_symptom(
        self,
        incident: GraphEntity,
        symptom: str,
        *,
        confidence: float | None = None,
        occurred_at: str | None = None,
    ) -> GraphEntity:
        entity = GraphEntity(SupportEntityType.SYMPTOM, symptom, symptom)
        props: dict[str, Any] = {}
        if confidence is not None:
            props["confidence"] = confidence
        self.relate(incident, entity, SupportRelation.HAS_SYMPTOM, occurred_at=occurred_at, **props)
        return entity

    def add_action(
        self,
        incident: GraphEntity,
        action: str,
        *,
        outcome: str = "unknown",
        contributor: str | None = None,
        occurred_at: str | None = None,
    ) -> GraphEntity:
        entity = GraphEntity(SupportEntityType.ACTION, action, action)
        self.relate(incident, entity, SupportRelation.TRIED, occurred_at=occurred_at, outcome=outcome)
        if outcome.lower() in {"failed", "unsuccessful", "no_change"}:
            self.relate(incident, entity, SupportRelation.FAILED_ACTION, occurred_at=occurred_at)
        if contributor:
            person = GraphEntity(SupportEntityType.PERSON, contributor, contributor)
            self.relate(person, entity, SupportRelation.CONTRIBUTED, occurred_at=occurred_at)
        return entity

    def add_resolution(
        self,
        incident: GraphEntity,
        resolution: str,
        *,
        resolver: str | None = None,
        root_cause: str = "",
        confidence: float | None = None,
        occurred_at: str | None = None,
    ) -> GraphEntity:
        props: dict[str, Any] = {"root_cause": root_cause}
        if confidence is not None:
            props["confidence"] = confidence
        entity = GraphEntity(SupportEntityType.RESOLUTION, f"{incident.key}:{resolution}", resolution, props)
        self.relate(incident, entity, SupportRelation.SUCCESSFUL_FIX, occurred_at=occurred_at)
        if resolver:
            person = GraphEntity(SupportEntityType.PERSON, resolver, resolver)
            self.relate(person, incident, SupportRelation.RESOLVED, occurred_at=occurred_at)
            self.relate(entity, person, SupportRelation.RESOLVED_BY, occurred_at=occurred_at)
        return entity

    def link_thread(
        self, incident: GraphEntity, channel_id: str, thread_ts: str, *, occurred_at: str | None = None
    ) -> GraphEntity:
        key = f"{channel_id}:{thread_ts}"
        thread = GraphEntity(
            SupportEntityType.SLACK_THREAD,
            key,
            key,
            {"channel_id": channel_id, "thread_ts": thread_ts},
        )
        self.relate(incident, thread, SupportRelation.DISCUSSED_IN, occurred_at=occurred_at)
        self.relate(thread, incident, SupportRelation.REFERENCES, occurred_at=occurred_at)
        return thread

    def related(
        self,
        entity: GraphEntity | str,
        *,
        depth: int = 1,
        since: str | None = None,
        order_by_recency: bool = False,
    ) -> list[dict[str, Any]]:
        """Return related graph facts, optionally recency-ordered with an age_days annotation.

        This is the retrieval-time half of the temporal graph: callers such as
        `SupportEvidenceService` use `order_by_recency=True` to surface the most
        recently confirmed fix first and to detect when a once-good fix is old
        enough that it should be re-verified rather than trusted blindly.
        """
        entity_id = entity if isinstance(entity, str) else entity.entity_id
        items = self.store.get_related(
            entity_id, max_depth=depth, since=since, order_by_recency=order_by_recency
        )
        for item in items:
            item["age_days"] = age_days(item.get("occurred_at"))
        return items

    def save(self) -> None:
        self.store.save()

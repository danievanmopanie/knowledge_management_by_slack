"""RAG for incident context – field-aware indexing and incident-level retrieval."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import Any, Iterable

from langchain_core.documents import Document

from src.core.config import settings
from src.knowledge.graphstore import GraphStore
from src.knowledge.incident_dedupe import (
    content_hash,
    filter_changed_incidents,
    load_hash_index,
    save_hash_index,
)
from src.knowledge.vectorstore import VectorStore
from src.reporting.incidents import Incident, load_all_incidents

logger = logging.getLogger(__name__)

INCIDENT_COLLECTION = "incidents"
FIELD_GROUPS = ("problem", "troubleshooting", "resolution")

# Per-field caps so large journals do not dominate embeddings.
FIELD_LIMITS = {
    "short_description": 500,
    "description": 1500,
    "work_notes": 2000,
    "comments": 1200,
    "resolution_notes": 1200,
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clip(text: str, limit: int) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …"


def _dedupe_values(values: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Remove blank or materially duplicated free-text values while preserving labels."""
    kept: list[tuple[str, str]] = []
    for label, value in values:
        value = _clean(value)
        if not value:
            continue
        lowered = value.lower()
        if any(lowered in existing.lower() or existing.lower() in lowered for _, existing in kept):
            continue
        kept.append((label, value))
    return kept


def _taxonomy_tail(inc: Incident) -> str:
    values: list[str] = []
    if inc.category:
        values.append(f"category: {_clean(inc.category)}")
    if inc.subcategory:
        values.append(f"subcategory: {_clean(inc.subcategory)}")
    if inc.location:
        values.append(f"location: {_clean(inc.location)}")
    if inc.assignment_group:
        values.append(f"assignment group: {_clean(inc.assignment_group)}")
    if inc.state:
        values.append(f"state: {_clean(inc.state)}")
    if inc.number:
        values.append(f"incident: {inc.number}")
    return " | ".join(values)


def _field_document(inc: Incident, field_group: str) -> str:
    """Build one semantically focused embedding document for an incident field group."""
    if field_group == "problem":
        values = _dedupe_values(
            [
                ("Short description", _clip(inc.short_description, FIELD_LIMITS["short_description"])),
                ("Description", _clip(inc.description, FIELD_LIMITS["description"])),
            ]
        )
    elif field_group == "troubleshooting":
        values = _dedupe_values(
            [
                ("Work notes", _clip(inc.work_notes, FIELD_LIMITS["work_notes"])),
                ("Comments", _clip(inc.comments, FIELD_LIMITS["comments"])),
            ]
        )
    elif field_group == "resolution":
        values = _dedupe_values(
            [("Resolution notes", _clip(inc.resolution_notes, FIELD_LIMITS["resolution_notes"]))]
        )
    else:
        raise ValueError(f"Unknown incident field group: {field_group}")

    if not values:
        return ""

    parts = [f"{label}: {value}" for label, value in values]
    tail = _taxonomy_tail(inc)
    if tail:
        parts.append(tail)
    return "\n".join(parts)


def _incident_to_text(inc: Incident) -> str:
    """Compatibility view combining the three focused field documents."""
    parts = []
    for field_group in FIELD_GROUPS:
        text = _field_document(inc, field_group)
        if text:
            parts.append(f"[{field_group}]\n{text}")
    return "\n\n".join(parts)


def _incident_metadata(inc: Incident, field_group: str) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "source": f"incident:{inc.number}",
        "doc_type": "incident",
        "number": inc.number,
        "field_group": field_group,
        "state": inc.state or "",
        "assignment_group": inc.assignment_group or "",
        "assigned_to": inc.assigned_to or "",
        "caller": inc.caller or "",
        "location": inc.location or "",
        "category": inc.category or "",
        "subcategory": inc.subcategory or "",
        "content_hash": content_hash(inc),
        "has_work_notes": bool(_clean(inc.work_notes)),
        "has_comments": bool(_clean(inc.comments)),
        "has_resolution_notes": bool(_clean(inc.resolution_notes)),
    }
    if inc.opened_at:
        meta["opened_at"] = inc.opened_at.isoformat()
    if inc.resolved_at:
        meta["resolved_at"] = inc.resolved_at.isoformat()
    return meta


def _link_incident_graph(graph: GraphStore, inc: Incident) -> None:
    node_id = f"incident:{inc.number}"
    graph.add_entity(
        node_id,
        entity_type="incident",
        name=inc.number,
        short_description=inc.short_description or "",
    )

    def link(value: str | None, etype: str, relation: str) -> None:
        if not value or not value.strip():
            return
        eid = f"{etype}:{value.strip().lower()}"
        graph.add_entity(eid, entity_type=etype, name=value.strip())
        graph.add_relation(node_id, eid, relation=relation)

    link(inc.assignment_group, "assignment_group", "assigned_to_group")
    link(inc.assigned_to, "person", "assigned_to")
    link(inc.caller, "person", "reported_by")
    link(inc.location, "location", "located_at")
    link(inc.category, "category", "categorised_as")
    link(inc.subcategory, "subcategory", "subcategorised_as")


def _aggregate_incident_hits(raw_docs: list[Document], k: int) -> list[Document]:
    """Aggregate field-level vector matches back to one ranked document per incident."""
    grouped: dict[str, list[Document]] = defaultdict(list)
    for doc in raw_docs:
        number = str((doc.metadata or {}).get("number") or "").strip()
        if number:
            grouped[number].append(doc)

    aggregated: list[Document] = []
    for number, docs in grouped.items():
        docs.sort(key=lambda d: float((d.metadata or {}).get("score", 0.0)), reverse=True)
        best = docs[0]
        best_meta = dict(best.metadata or {})

        by_group: dict[str, Document] = {}
        for doc in docs:
            group = str((doc.metadata or {}).get("field_group") or "combined")
            by_group.setdefault(group, doc)

        ordered_groups = [g for g in FIELD_GROUPS if g in by_group]
        ordered_groups.extend(g for g in by_group if g not in ordered_groups)
        sections: list[str] = []
        field_scores: dict[str, float] = {}
        for group in ordered_groups:
            doc = by_group[group]
            score = float((doc.metadata or {}).get("score", 0.0))
            field_scores[group] = score
            sections.append(f"{group.title()} match (relevance={score:.2f})\n{doc.page_content.strip()}")

        best_meta["matched_fields"] = ",".join(ordered_groups)
        best_meta["field_scores_json"] = json.dumps(field_scores, sort_keys=True)
        best_meta["score"] = float(best_meta.get("score", 0.0))
        best_meta["semantic_score"] = best_meta["score"]
        best_meta["chunk_id"] = f"incident-{number}-aggregate"
        aggregated.append(Document(page_content="\n\n".join(sections), metadata=best_meta))

    aggregated.sort(key=lambda d: float((d.metadata or {}).get("score", 0.0)), reverse=True)
    return aggregated[:k]


class IncidentRAG:
    """Incident RAG with field-aware BGE embeddings and incident-level ranking.

    Three semantic views are indexed independently:
    - problem: Short Description + Description
    - troubleshooting: Work Notes + Comments
    - resolution: Resolution Notes

    Retrieval searches those focused documents, then aggregates the matches back
    to one incident so callers still receive an incident-level result list.
    """

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        graph_store: GraphStore | None = None,
    ):
        self.vector_store = vector_store or VectorStore(
            collection_name=INCIDENT_COLLECTION,
            embedding_purpose="incident",
        )
        self.graph_store = graph_store or GraphStore()

    def index_incidents(
        self,
        incidents: Iterable[Incident],
        *,
        force: bool = False,
    ) -> dict[str, int]:
        """Embed new/changed incidents as focused field documents."""
        incident_list = list(incidents)
        if not incident_list:
            return {
                "indexed": 0,
                "indexed_documents": 0,
                "skipped_unchanged": 0,
                "collection_total": self.vector_store.count(),
            }

        if force:
            to_index = incident_list
            skipped = 0
            index = {inc.number: content_hash(inc) for inc in incident_list}
            logger.info("Force reindex: %s incidents", len(to_index))
        else:
            existing = load_hash_index()
            to_index, unchanged, index = filter_changed_incidents(
                incident_list, existing=existing
            )
            skipped = len(unchanged)

        docs: list[str] = []
        metas: list[dict[str, Any]] = []
        ids: list[str] = []
        indexed_incidents = 0

        for inc in to_index:
            incident_docs = 0
            # Remove legacy combined and all focused IDs before upserting this incident.
            stale_ids = [f"incident-{inc.number}"] + [
                f"incident-{inc.number}-{field_group}" for field_group in FIELD_GROUPS
            ]
            try:
                self.vector_store.delete_documents(stale_ids)
            except Exception:
                logger.debug("Could not pre-delete incident vector IDs for %s", inc.number, exc_info=True)

            for field_group in FIELD_GROUPS:
                text = _field_document(inc, field_group)
                if not text:
                    continue
                docs.append(text)
                metas.append(_incident_metadata(inc, field_group))
                ids.append(f"incident-{inc.number}-{field_group}")
                incident_docs += 1

            if incident_docs:
                indexed_incidents += 1
                _link_incident_graph(self.graph_store, inc)

        if docs:
            logger.info(
                "Indexing %s field documents across %s changed incidents with model '%s' "
                "(batch_size=%s); skipped %s unchanged",
                len(docs),
                indexed_incidents,
                settings.incident_embedding_model,
                settings.incident_embedding_batch_size,
                skipped,
            )
            self.vector_store.add_documents(
                docs,
                metadatas=metas,
                ids=ids,
                batch_size=settings.incident_embedding_batch_size,
            )
            self.graph_store.save()
        else:
            logger.info("No changed incidents to embed (skipped %s unchanged)", skipped)

        save_hash_index(index)

        return {
            "indexed": indexed_incidents,
            "indexed_documents": len(docs),
            "skipped_unchanged": skipped,
            "collection_total": self.vector_store.count(),
        }

    def index_from_disk(self, force: bool = False) -> dict[str, int]:
        incidents = load_all_incidents()
        logger.info("Loaded %s incidents from disk for reindex", len(incidents))
        return self.index_incidents(incidents, force=force)

    def similar_incidents(
        self,
        query: str,
        k: int = 5,
        where: dict | None = None,
    ) -> list[Document]:
        """Search focused field documents and return one aggregated result per incident."""
        query = _clean(query)
        if not query:
            return []
        # Fetch extra field hits because several can belong to the same incident.
        raw_k = max(k * len(FIELD_GROUPS), k)
        raw_docs = self.vector_store.similarity_search(query, k=raw_k, where=where)
        return _aggregate_incident_hits(raw_docs, k)

    def build_context(
        self,
        query: str,
        k: int = 5,
        include_graph: bool = True,
        max_chars: int = 4500,
    ) -> str:
        docs = self.similar_incidents(query, k=k)
        if not docs:
            return ""

        parts = ["### Similar past incidents"]
        for i, doc in enumerate(docs, 1):
            meta = doc.metadata or {}
            header = (
                f"[{i}] {meta.get('number', '?')} "
                f"| {meta.get('state', '')} "
                f"| group={meta.get('assignment_group', '')} "
                f"| loc={meta.get('location', '')}"
            )
            matched_fields = str(meta.get("matched_fields") or "").replace(",", ", ")
            if matched_fields:
                header += f" | matched={matched_fields}"
            score = meta.get("score")
            if score is not None:
                header += f" | relevance={float(score):.2f}"
            parts.append(header)
            parts.append(doc.page_content.strip())
            parts.append("")

        if include_graph:
            entities = []
            for doc in docs[:3]:
                num = (doc.metadata or {}).get("number")
                group = (doc.metadata or {}).get("assignment_group")
                if num:
                    entities.append(f"incident:{num}")
                if group:
                    entities.append(f"assignment_group:{group.strip().lower()}")

            related_lines = []
            seen = set()
            for ent in entities:
                for rel in self.graph_store.get_related(ent, max_depth=1):
                    key = (rel.get("entity"), rel.get("relation"))
                    if key in seen:
                        continue
                    seen.add(key)
                    related_lines.append(
                        f"- ({rel.get('relation')}) {rel.get('entity')} "
                        f"[{rel.get('type', 'entity')}]"
                    )
            if related_lines:
                parts.append("### Related entities")
                parts.extend(related_lines[:20])

        text = "\n".join(parts).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[Incident context truncated]"
        return text

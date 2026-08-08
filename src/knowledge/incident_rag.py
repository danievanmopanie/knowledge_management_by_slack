"""RAG for incident context – index and retrieve similar past incidents."""

from __future__ import annotations

import logging
import re
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

# Per-field caps so large journals do not dominate embeddings
FIELD_LIMITS = {
    "short_description": 500,
    "description": 1500,
    "work_notes": 2000,
    "comments": 1200,
    "resolution_notes": 1200,
}


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def _clip(text: str, limit: int) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …"


def _dedupe_append(parts: list[str], label: str, value: str) -> None:
    value = _clean(value)
    if not value:
        return
    lowered = value.lower()
    for existing in parts:
        if lowered in existing.lower() or existing.lower() in lowered:
            return
    parts.append(f"{label}: {value}")


def _incident_to_text(inc: Incident) -> str:
    """
    Embedding text from free-text columns:
    Short Description, Description, Resolution Notes, Work Notes, Comments.
    """
    free_text_parts: list[str] = []

    short_desc = _clip(inc.short_description, FIELD_LIMITS["short_description"])
    if short_desc:
        free_text_parts.append(short_desc)

    description = _clip(inc.description, FIELD_LIMITS["description"])
    if description and description.lower() != short_desc.lower():
        free_text_parts.append(f"Description: {description}")

    resolution = _clip(inc.resolution_notes, FIELD_LIMITS["resolution_notes"])
    _dedupe_append(free_text_parts, "Resolution", resolution)

    work_notes = _clip(inc.work_notes, FIELD_LIMITS["work_notes"])
    _dedupe_append(free_text_parts, "Work notes", work_notes)

    comments = _clip(inc.comments, FIELD_LIMITS["comments"])
    _dedupe_append(free_text_parts, "Comments", comments)

    taxonomy: list[str] = []
    if inc.category:
        taxonomy.append(f"category: {_clean(inc.category)}")
    if inc.subcategory:
        taxonomy.append(f"subcategory: {_clean(inc.subcategory)}")
    if inc.location:
        taxonomy.append(f"location: {_clean(inc.location)}")
    if inc.assignment_group:
        taxonomy.append(f"assignment group: {_clean(inc.assignment_group)}")

    tail: list[str] = []
    if inc.number:
        tail.append(f"incident {inc.number}")
    if inc.state:
        tail.append(f"state: {_clean(inc.state)}")

    parts: list[str] = []
    if free_text_parts:
        parts.append("\n".join(free_text_parts))
    if taxonomy:
        parts.append(" | ".join(taxonomy))
    if tail:
        parts.append(" | ".join(tail))

    return "\n".join(parts)


def _incident_metadata(inc: Incident) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "source": f"incident:{inc.number}",
        "doc_type": "incident",
        "number": inc.number,
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


class IncidentRAG:
    """
    Incident RAG using lightweight BGE embeddings + Qwen3 for answers.

    Free-text embedded: Short Description, Description, Work Notes,
    Comments, Resolution Notes.

    Daily CSV uploads: content-hash dedupe skips unchanged rows so we do not
    re-embed identical ServiceNow exports.
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
        """
        Embed and store incidents in batches.

        By default only new or changed rows are embedded (content-hash check).
        Pass force=True to re-embed everything.
        """
        incident_list = list(incidents)
        if not incident_list:
            return {
                "indexed": 0,
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

        for inc in to_index:
            text = _incident_to_text(inc)
            if not text.strip():
                continue
            docs.append(text)
            metas.append(_incident_metadata(inc))
            ids.append(f"incident-{inc.number}")
            _link_incident_graph(self.graph_store, inc)

        count = len(docs)
        if docs:
            try:
                self.vector_store._collection.delete(ids=ids)
            except Exception:
                pass

            logger.info(
                "Indexing %s changed incidents with model '%s' (batch_size=%s); skipped %s unchanged",
                count,
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
            logger.info(
                "No changed incidents to embed (skipped %s unchanged)",
                skipped,
            )

        # Persist full hash index so tomorrow's export can skip unchanged rows
        save_hash_index(index)

        return {
            "indexed": count,
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
        query = _clean(query)
        return self.vector_store.similarity_search(query, k=k, where=where)

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

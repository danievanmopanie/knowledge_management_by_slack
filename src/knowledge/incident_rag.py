"""RAG for incident context – index and retrieve similar past incidents."""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from langchain_core.documents import Document

from src.knowledge.graphstore import GraphStore
from src.knowledge.vectorstore import VectorStore
from src.reporting.incidents import Incident, load_all_incidents

logger = logging.getLogger(__name__)

INCIDENT_COLLECTION = "incidents"


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def _incident_to_text(inc: Incident) -> str:
    """
    Build an embedding-optimised representation of an incident.

    Priority order for semantic matching:
    1. Problem summary + description (what failed / symptoms)
    2. Category / subcategory (problem class)
    3. Location + assignment group (operational context)
    4. Identifiers last (useful for display, weak for pure semantics)
    """
    summary = _clean(inc.short_description)
    description = _clean(inc.description)
    category = _clean(inc.category)
    subcategory = _clean(inc.subcategory)
    location = _clean(inc.location)
    group = _clean(inc.assignment_group)

    # Lead with the actual problem text – this drives similarity
    head: list[str] = []
    if summary:
        head.append(summary)
    if description and description.lower() != summary.lower():
        # Cap very long work notes so embeddings focus on the issue
        head.append(description[:1200])

    taxonomy: list[str] = []
    if category:
        taxonomy.append(f"category: {category}")
    if subcategory:
        taxonomy.append(f"subcategory: {subcategory}")
    if location:
        taxonomy.append(f"location: {location}")
    if group:
        taxonomy.append(f"assignment group: {group}")

    tail: list[str] = []
    if inc.number:
        tail.append(f"incident {inc.number}")
    if inc.state:
        tail.append(f"state: {_clean(inc.state)}")

    parts = []
    if head:
        parts.append(" ".join(head))
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
    }
    if inc.opened_at:
        meta["opened_at"] = inc.opened_at.isoformat()
    if inc.resolved_at:
        meta["resolved_at"] = inc.resolved_at.isoformat()
    return meta


def _link_incident_graph(graph: GraphStore, inc: Incident) -> None:
    """Create lightweight entity relationships for an incident."""
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
    Incident-focused RAG layer.

    Uses a dedicated embedding model (see INCIDENT_EMBEDDING_MODEL) optimised
    for short technical problem statements, separate from general knowledge embeddings.
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

    def index_incidents(self, incidents: Iterable[Incident]) -> dict[str, int]:
        """Embed and store incidents; update graph relationships."""
        docs: list[str] = []
        metas: list[dict[str, Any]] = []
        ids: list[str] = []

        count = 0
        for inc in incidents:
            text = _incident_to_text(inc)
            if not text.strip():
                continue
            docs.append(text)
            metas.append(_incident_metadata(inc))
            ids.append(f"incident-{inc.number}")
            _link_incident_graph(self.graph_store, inc)
            count += 1

        if docs:
            try:
                self.vector_store._collection.delete(ids=ids)
            except Exception:
                pass
            self.vector_store.add_documents(docs, metadatas=metas, ids=ids)
            self.graph_store.save()

        logger.info("Indexed %s incidents into incident RAG", count)
        return {"indexed": count, "collection_total": self.vector_store.count()}

    def index_from_disk(self) -> dict[str, int]:
        """Load all known incident CSVs and index them."""
        incidents = load_all_incidents()
        return self.index_incidents(incidents)

    def similar_incidents(
        self,
        query: str,
        k: int = 5,
        where: dict | None = None,
    ) -> list[Document]:
        """Semantic search over past incidents."""
        # Light query normalisation – keep technician language intact
        query = _clean(query)
        return self.vector_store.similarity_search(query, k=k, where=where)

    def build_context(
        self,
        query: str,
        k: int = 5,
        include_graph: bool = True,
        max_chars: int = 4000,
    ) -> str:
        """Build a context block of similar past incidents for the LLM."""
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

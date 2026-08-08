"""RAG for incident context – index and retrieve similar past incidents."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from langchain_core.documents import Document

from src.knowledge.graphstore import GraphStore
from src.knowledge.vectorstore import VectorStore
from src.reporting.incidents import Incident, load_all_incidents

logger = logging.getLogger(__name__)

INCIDENT_COLLECTION = "incidents"


def _incident_to_text(inc: Incident) -> str:
    """Build a rich text representation for embedding."""
    parts = [
        f"Incident {inc.number}",
        f"Summary: {inc.short_description}" if inc.short_description else "",
        f"Description: {inc.description}" if inc.description else "",
        f"Category: {inc.category}" if inc.category else "",
        f"Subcategory: {inc.subcategory}" if inc.subcategory else "",
        f"State: {inc.state}" if inc.state else "",
        f"Assignment group: {inc.assignment_group}" if inc.assignment_group else "",
        f"Assigned to: {inc.assigned_to}" if inc.assigned_to else "",
        f"Caller: {inc.caller}" if inc.caller else "",
        f"Location: {inc.location}" if inc.location else "",
    ]
    if inc.opened_at:
        parts.append(f"Opened: {inc.opened_at.isoformat()}")
    if inc.resolved_at:
        parts.append(f"Resolved: {inc.resolved_at.isoformat()}")
    return "\n".join(p for p in parts if p)


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

    - Indexes incidents into a dedicated Chroma collection
    - Links entities in the knowledge graph
    - Retrieves similar past incidents for a query / live issue description
    """

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        graph_store: GraphStore | None = None,
    ):
        self.vector_store = vector_store or VectorStore(collection_name=INCIDENT_COLLECTION)
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
            # Chroma upsert behaviour: add with stable IDs; re-index may duplicate
            # if IDs already exist depending on client version. We delete first when possible.
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
        return self.vector_store.similarity_search(query, k=k, where=where)

    def build_context(
        self,
        query: str,
        k: int = 5,
        include_graph: bool = True,
        max_chars: int = 4000,
    ) -> str:
        """
        Build a ready-to-use context block of similar past incidents
        (+ optional graph neighbours) for the LLM.
        """
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
            # Expand from top incident numbers / groups
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

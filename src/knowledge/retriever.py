"""Hybrid RAG retriever with access-controlled vector results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.documents import Document

from src.core.context import RequestContext
from src.knowledge.graphstore import GraphStore
from src.knowledge.vectorstore import VectorStore
from src.security.access import can_read


@dataclass
class RetrievalResult:
    documents: list[Document] = field(default_factory=list)
    graph_context: list[dict[str, Any]] = field(default_factory=list)
    query: str = ""

    def to_context_string(self, max_chars: int = 6000) -> str:
        parts: list[str] = []
        if self.documents:
            parts.append("### Relevant Knowledge Articles & Notes")
            for i, doc in enumerate(self.documents, 1):
                source = doc.metadata.get("source", "unknown")
                score = doc.metadata.get("score")
                header = f"[{i}] Source: {source}"
                if score is not None:
                    header += f" (relevance: {score:.2f})"
                parts.extend([header, doc.page_content.strip(), ""])
        if self.graph_context:
            parts.append("### Related Entities & Relationships")
            for item in self.graph_context:
                parts.append(
                    f"- ({item.get('relation', 'related_to')}) {item.get('entity')} "
                    f"[{item.get('type', 'entity')}]"
                )
            parts.append("")
        text = "\n".join(parts).strip()
        return text if len(text) <= max_chars else text[:max_chars] + "\n\n[Context truncated]"


class HybridRetriever:
    def __init__(self, vector_store: VectorStore | None = None, graph_store: GraphStore | None = None):
        self.vector_store = vector_store or VectorStore()
        self.graph_store = graph_store or GraphStore()

    def retrieve(
        self,
        query: str,
        k: int = 5,
        graph_depth: int = 1,
        where: dict | None = None,
        context: RequestContext | None = None,
    ) -> RetrievalResult:
        # Over-fetch before ACL filtering so denied chunks do not crowd out permitted ones.
        candidates = self.vector_store.similarity_search(query, k=max(k * 5, k), where=where)
        documents = [doc for doc in candidates if can_read(doc.metadata, context)][:k]

        graph_context: list[dict[str, Any]] = []
        candidate_entities = set(self.graph_store.search_entities(query, limit=5))
        for doc in documents:
            entities = doc.metadata.get("entities") or []
            if isinstance(entities, str):
                entities = [e.strip() for e in entities.split(",") if e.strip()]
            candidate_entities.update(entities)
        for entity in list(candidate_entities)[:8]:
            graph_context.extend(self.graph_store.get_related(entity, max_depth=graph_depth))

        seen: set[tuple[Any, Any]] = set()
        unique_graph = []
        for item in graph_context:
            key = (item.get("entity"), item.get("relation"))
            if key not in seen:
                seen.add(key)
                unique_graph.append(item)
        return RetrievalResult(documents=documents, graph_context=unique_graph, query=query)

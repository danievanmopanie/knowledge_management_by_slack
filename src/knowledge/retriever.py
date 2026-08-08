"""Hybrid RAG retriever: Vector similarity + lightweight Graph expansion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.documents import Document

from src.knowledge.graphstore import GraphStore
from src.knowledge.vectorstore import VectorStore


@dataclass
class RetrievalResult:
    """Combined result from hybrid retrieval."""
    documents: list[Document] = field(default_factory=list)
    graph_context: list[dict[str, Any]] = field(default_factory=list)
    query: str = ""

    def to_context_string(self, max_chars: int = 6000) -> str:
        """Format retrieved knowledge into a single context block for the LLM."""
        parts: list[str] = []

        if self.documents:
            parts.append("### Relevant Knowledge Articles & Notes")
            for i, doc in enumerate(self.documents, 1):
                source = doc.metadata.get("source", "unknown")
                score = doc.metadata.get("score")
                header = f"[{i}] Source: {source}"
                if score is not None:
                    header += f" (relevance: {score:.2f})"
                parts.append(header)
                parts.append(doc.page_content.strip())
                parts.append("")

        if self.graph_context:
            parts.append("### Related Entities & Relationships")
            for item in self.graph_context:
                rel = item.get("relation", "related_to")
                entity = item.get("entity")
                etype = item.get("type", "entity")
                parts.append(f"- ({rel}) {entity} [{etype}]")
            parts.append("")

        text = "\n".join(parts).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[Context truncated]"
        return text


class HybridRetriever:
    """
    Hybrid retrieval pipeline:

    1. Vector search (semantic similarity over document chunks)
    2. Lightweight graph expansion (related entities)
    3. Merge into a single context for the agent
    """

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        graph_store: GraphStore | None = None,
    ):
        self.vector_store = vector_store or VectorStore()
        self.graph_store = graph_store or GraphStore()

    def retrieve(
        self,
        query: str,
        k: int = 5,
        graph_depth: int = 1,
        where: dict | None = None,
    ) -> RetrievalResult:
        # 1. Vector similarity search
        documents = self.vector_store.similarity_search(query, k=k, where=where)

        # 2. Graph expansion – extract potential entity names from the query
        #    and from top document metadata, then expand relationships
        graph_context: list[dict[str, Any]] = []
        candidate_entities = set(self.graph_store.search_entities(query, limit=5))

        for doc in documents:
            # If ingest stored entity mentions in metadata, use them
            entities = doc.metadata.get("entities") or []
            if isinstance(entities, str):
                entities = [e.strip() for e in entities.split(",") if e.strip()]
            candidate_entities.update(entities)

        for entity in list(candidate_entities)[:8]:
            related = self.graph_store.get_related(entity, max_depth=graph_depth)
            graph_context.extend(related)

        # Deduplicate graph results
        seen = set()
        unique_graph = []
        for item in graph_context:
            key = (item.get("entity"), item.get("relation"))
            if key not in seen:
                seen.add(key)
                unique_graph.append(item)

        return RetrievalResult(
            documents=documents,
            graph_context=unique_graph,
            query=query,
        )

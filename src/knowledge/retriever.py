"""Governed hybrid retrieval service with access-controlled evidence."""

from __future__ import annotations

from typing import Any

from src.core.audit import AuditStore
from src.core.context import RequestContext
from src.knowledge.graphstore import GraphStore
from src.knowledge.lexical import lexical_search, reciprocal_rank_fusion
from src.knowledge.query_understanding import understand_query
from src.knowledge.retrieval_models import RetrievalCandidate, RetrievalQuery, RetrievalResult
from src.knowledge.vectorstore import VectorStore
from src.security.access import can_read


class HybridRetriever:
    """Single deterministic entry point for semantic + lexical knowledge retrieval."""

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        graph_store: GraphStore | None = None,
        audit_store: AuditStore | None = None,
    ):
        self.vector_store = vector_store or VectorStore()
        self.graph_store = graph_store or GraphStore()
        self.audit_store = audit_store or AuditStore()

    def search(self, request: RetrievalQuery) -> RetrievalResult:
        hints = understand_query(request.text)
        candidate_limit = max(request.limit * 5, request.limit)

        semantic_raw = self.vector_store.similarity_search(
            hints.normalized,
            k=candidate_limit,
            where=request.where,
        )
        semantic = [
            doc for doc in semantic_raw if can_read(doc.metadata, request.context)
        ]

        lexical_corpus_raw = self.vector_store.all_documents(where=request.where)
        lexical_corpus = [
            doc for doc in lexical_corpus_raw if can_read(doc.metadata, request.context)
        ]
        lexical = lexical_search(
            hints.normalized,
            lexical_corpus,
            limit=candidate_limit,
            exact_terms=hints.exact_terms,
        )

        selected = reciprocal_rank_fusion(
            semantic,
            lexical,
            limit=request.limit,
        )
        candidates = [
            RetrievalCandidate.from_document(document, rank)
            for rank, document in enumerate(selected, 1)
        ]

        if request.context is not None:
            denied_semantic = len(semantic_raw) - len(semantic)
            denied_lexical = len(lexical_corpus_raw) - len(lexical_corpus)
            self.audit_store.record(
                request.context,
                action="knowledge.retrieve",
                outcome="success",
                target_type="knowledge",
                metadata={
                    "semantic_candidates": len(semantic),
                    "lexical_candidates": len(lexical),
                    "returned_count": len(candidates),
                    "denied_semantic_count": denied_semantic,
                    "denied_lexical_count": denied_lexical,
                    "exact_terms": list(hints.exact_terms),
                    "evidence_ids": [candidate.evidence_id for candidate in candidates],
                },
            )

        graph_context = self._graph_context(request, selected)
        return RetrievalResult(
            candidates=candidates,
            graph_context=graph_context,
            query=request.text,
        )

    def retrieve(
        self,
        query: str,
        k: int = 5,
        graph_depth: int = 1,
        where: dict | None = None,
        context: RequestContext | None = None,
    ) -> RetrievalResult:
        """Compatibility wrapper; new callers should construct `RetrievalQuery`."""
        return self.search(
            RetrievalQuery(
                text=query,
                context=context,
                limit=k,
                graph_depth=graph_depth,
                where=where,
            )
        )

    def _graph_context(self, request: RetrievalQuery, documents: list[Any]) -> list[dict[str, Any]]:
        candidate_entities = set(self.graph_store.search_entities(request.text, limit=5))
        for doc in documents:
            entities = doc.metadata.get("entities") or []
            if isinstance(entities, str):
                entities = [e.strip() for e in entities.split(",") if e.strip()]
            candidate_entities.update(entities)

        graph_context: list[dict[str, Any]] = []
        for entity in list(candidate_entities)[:8]:
            graph_context.extend(
                self.graph_store.get_related(entity, max_depth=request.graph_depth)
            )

        seen: set[tuple[Any, Any]] = set()
        unique_graph: list[dict[str, Any]] = []
        for item in graph_context:
            key = (item.get("entity"), item.get("relation"))
            if key not in seen:
                seen.add(key)
                unique_graph.append(item)
        return unique_graph

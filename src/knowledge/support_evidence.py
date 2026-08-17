"""Three-layer evidence assembly for the collaborative frontend-support agent.

Layers:
1. Governed knowledge retrieval (articles, notes, uploaded documents)
2. Historical incident vector retrieval
3. Typed, temporally-aware support graph context (people, symptoms, actions,
   resolutions, ordered by recency with an explicit staleness signal)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.config import settings
from src.core.context import RequestContext
from src.knowledge.incident_rag import IncidentRAG
from src.knowledge.retrieval_models import RetrievalCandidate, RetrievalQuery
from src.knowledge.retriever import HybridRetriever
from src.knowledge.support_graph import SupportKnowledgeGraph


def _recency_label(age_days: int | None) -> str:
    if age_days is None:
        return ""
    if age_days == 0:
        return " (today)"
    if age_days == 1:
        return " (1 day ago)"
    if age_days < 30:
        return f" ({age_days} days ago)"
    if age_days < 365:
        return f" ({age_days // 30} month(s) ago)"
    return f" ({age_days // 365} year(s) ago)"


@dataclass
class SupportEvidencePackage:
    query: str
    governed_candidates: list[RetrievalCandidate] = field(default_factory=list)
    governed_context: str = ""
    incident_context: str = ""
    graph_context: str = ""
    incident_sources: set[str] = field(default_factory=set)
    governed_should_answer: bool = False
    confidence_score: float = 0.0

    @property
    def has_evidence(self) -> bool:
        return bool(self.governed_context or self.incident_context or self.graph_context)

    def to_prompt_context(self, max_chars: int = 9000) -> str:
        parts: list[str] = []
        if self.incident_context:
            parts.extend([self.incident_context, "---"])
        if self.graph_context:
            parts.extend([self.graph_context, "---"])
        if self.governed_context:
            parts.extend(["### Governed knowledge\n" + self.governed_context, "---"])
        text = "\n\n".join(parts).strip()
        if len(text) > max_chars:
            return text[:max_chars] + "\n\n[Support evidence truncated]"
        return text


class SupportEvidenceService:
    """Build one compact evidence package for a frontend-support turn."""

    def __init__(
        self,
        retriever: HybridRetriever | None = None,
        incident_rag: IncidentRAG | None = None,
        support_graph: SupportKnowledgeGraph | None = None,
    ):
        self.retriever = retriever or HybridRetriever()
        self.incident_rag = incident_rag or IncidentRAG()
        self.support_graph = support_graph or SupportKnowledgeGraph(self.incident_rag.graph_store)

    def build(self, query: str, context: RequestContext, *, limit: int = 5) -> SupportEvidencePackage:
        governed = self.retriever.search(
            RetrievalQuery(text=query, context=context, limit=limit, graph_depth=1)
        )

        incident_docs = self.incident_rag.similar_incidents(query, k=limit)
        incident_sources: set[str] = set()
        graph_facts: list[tuple[str, str]] = []
        seen_graph: set[tuple[str, str]] = set()

        for doc in incident_docs:
            meta = doc.metadata or {}
            number = str(meta.get("number") or "").strip()
            if not number:
                continue
            incident_sources.add(f"incident:{number}")
            incident_id = f"incident:{number}"
            for item in self.support_graph.related(incident_id, depth=2, order_by_recency=True):
                relation = str(item.get("relation") or "related_to")
                entity = str(item.get("entity") or "")
                key = (relation, entity)
                if not entity or key in seen_graph:
                    continue
                seen_graph.add(key)
                props = item.get("properties") or {}
                name = props.get("name", entity)
                etype = item.get("type", "entity")
                occurred_at = str(item.get("occurred_at") or "")
                age = item.get("age_days")
                line = f"- ({relation}) {name} [{etype}]{_recency_label(age)}"
                if relation == "successful_fix" and age is not None and age > settings.support_graph_stale_fix_days:
                    line += " — not reconfirmed recently; verify it still applies before relying on it"
                graph_facts.append((occurred_at, line))

        # Sort the merged facts from every matched incident by recency so the most
        # recently confirmed evidence surfaces first, not just the first incident found.
        graph_facts.sort(key=lambda item: item[0], reverse=True)
        graph_lines = [line for _, line in graph_facts]

        graph_context = ""
        if graph_lines:
            graph_context = "### Collective support graph (most recent first)\n" + "\n".join(graph_lines[:30])

        return SupportEvidencePackage(
            query=query,
            governed_candidates=governed.candidates,
            governed_context=governed.to_context_string() if governed.should_answer else "",
            incident_context=self.incident_rag.build_context(
                query,
                k=limit,
                include_graph=False,
                max_chars=4500,
            ),
            graph_context=graph_context,
            incident_sources=incident_sources,
            governed_should_answer=governed.should_answer,
            confidence_score=governed.confidence_score,
        )

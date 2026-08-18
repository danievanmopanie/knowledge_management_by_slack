"""Three-layer evidence assembly for the collaborative frontend-support agent.

Layers:
1. Governed knowledge retrieval (articles, notes, uploaded documents)
2. Historical incident vector retrieval
3. Typed support graph context (people, symptoms, actions, resolutions)

Conversational support is latency-sensitive. Governed and incident retrieval run in
parallel, incident hits are reused rather than searched twice, and short-lived
context-scoped caching avoids repeating identical work during active incidents.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from src.core.config import settings
from src.core.context import RequestContext
from src.knowledge.incident_rag import IncidentRAG
from src.knowledge.retrieval_models import RetrievalCandidate, RetrievalQuery
from src.knowledge.retriever import HybridRetriever
from src.knowledge.support_graph import SupportKnowledgeGraph

logger = logging.getLogger(__name__)

CONVERSATIONAL_LIMIT = 3
PROMPT_CONTEXT_MAX_CHARS = 4500
INCIDENT_CONTEXT_MAX_CHARS = 2600
EVIDENCE_CACHE_TTL_SECONDS = 300
EVIDENCE_CACHE_MAX_ENTRIES = 128
_PRIVATE_EVENT_RE = re.compile(r"^- technician \[[^\]]+\]:\s*(.+)$", re.MULTILINE)


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

    def to_prompt_context(self, max_chars: int = PROMPT_CONTEXT_MAX_CHARS) -> str:
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


def _normalise_query(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _retrieval_text(query: str) -> str:
    """Use the latest technician utterance for private-vector search, not coaching boilerplate.

    Private coaching deliberately gives the LLM the full recent DM conversation. That text also
    contains control instructions and older turns, which makes a poor embedding query. Retrieval
    should instead follow the latest technician symptom while the complete conversation remains
    available to generation through ``SupportEvidencePackage.query`` / the caller's message.
    """
    if not query.lstrip().startswith("PRIVATE COACHING SESSION."):
        return query
    matches = _PRIVATE_EVENT_RE.findall(query)
    if not matches:
        return query
    return matches[-1].strip() or query


def _context_scope(context: RequestContext) -> tuple:
    """Scope cache entries so permission-sensitive governed evidence cannot leak."""
    return (
        context.channel_id or "",
        context.user_id or "",
        tuple(sorted(context.roles or ())),
    )


def _incident_relevance(doc) -> float:
    """Return the cosine-similarity score attached by the incident vector store."""
    try:
        return float((doc.metadata or {}).get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _filter_incident_docs(docs: list) -> list:
    """Keep only incident matches strong enough to become technician-facing evidence."""
    threshold = settings.frontend_incident_min_relevance
    return [doc for doc in docs if _incident_relevance(doc) >= threshold]


def _incident_context_from_docs(docs: list, *, max_chars: int = INCIDENT_CONTEXT_MAX_CHARS) -> str:
    """Render already-retrieved incident docs without issuing a second vector search."""
    if not docs:
        return ""
    parts = ["### Similar past incidents"]
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata or {}
        header = (
            f"[{i}] {meta.get('number', '?')} | {meta.get('state', '')} "
            f"| group={meta.get('assignment_group', '')} | loc={meta.get('location', '')}"
        )
        matched_fields = str(meta.get("matched_fields") or "").replace(",", ", ")
        if matched_fields:
            header += f" | matched={matched_fields}"
        score = meta.get("score")
        if score is not None:
            header += f" | relevance={float(score):.2f}"
        parts.extend([header, doc.page_content.strip(), ""])
    text = "\n".join(parts).strip()
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[Incident context truncated]"
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
        self._cache: dict[tuple, tuple[float, SupportEvidencePackage]] = {}
        self._cache_lock = threading.RLock()

    def _cache_key(self, query: str, context: RequestContext, limit: int) -> tuple:
        return (_normalise_query(query), _context_scope(context), limit)

    def _get_cached(self, key: tuple) -> SupportEvidencePackage | None:
        now = time.monotonic()
        with self._cache_lock:
            item = self._cache.get(key)
            if item is None:
                return None
            created, package = item
            if now - created > EVIDENCE_CACHE_TTL_SECONDS:
                self._cache.pop(key, None)
                return None
            return package

    def _put_cached(self, key: tuple, package: SupportEvidencePackage) -> None:
        with self._cache_lock:
            if len(self._cache) >= EVIDENCE_CACHE_MAX_ENTRIES:
                oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
                self._cache.pop(oldest_key, None)
            self._cache[key] = (time.monotonic(), package)

    def build(
        self,
        query: str,
        context: RequestContext,
        *,
        limit: int = CONVERSATIONAL_LIMIT,
    ) -> SupportEvidencePackage:
        limit = min(max(1, int(limit)), CONVERSATIONAL_LIMIT)
        cache_key = self._cache_key(query, context, limit)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        retrieval_text = _retrieval_text(query)
        if retrieval_text != query:
            logger.info(
                "Frontend private retrieval uses latest technician turn chars=%s full_context_chars=%s",
                len(retrieval_text),
                len(query),
            )
        retrieval_query = RetrievalQuery(
            text=retrieval_text,
            context=context,
            limit=limit,
            graph_depth=1,
        )

        # These searches are independent and usually dominate retrieval latency.
        # Run them concurrently, then enrich only the already-returned incident hits.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="frontend-evidence") as pool:
            governed_future = pool.submit(self.retriever.search, retrieval_query)
            incident_future = pool.submit(self.incident_rag.similar_incidents, retrieval_text, limit)
            governed = governed_future.result()
            raw_incident_docs = incident_future.result()

        governed_allowed = bool(
            governed.should_answer
            and governed.confidence_score >= settings.frontend_governed_min_confidence
        )
        incident_docs = _filter_incident_docs(raw_incident_docs)

        if governed.should_answer and not governed_allowed:
            logger.info(
                "Frontend evidence gate dropped governed context confidence=%.3f threshold=%.3f",
                governed.confidence_score,
                settings.frontend_governed_min_confidence,
            )
        if len(incident_docs) != len(raw_incident_docs):
            logger.info(
                "Frontend evidence gate kept %s/%s incident matches threshold=%.3f scores=%s",
                len(incident_docs),
                len(raw_incident_docs),
                settings.frontend_incident_min_relevance,
                [round(_incident_relevance(doc), 3) for doc in raw_incident_docs],
            )

        incident_sources: set[str] = set()
        graph_lines: list[str] = []
        seen_graph: set[tuple[str, str]] = set()

        # Graph expansion must only occur from incidents that survived the relevance gate;
        # otherwise a weak vector match can pull unrelated entities back into the prompt.
        for doc in incident_docs:
            meta = doc.metadata or {}
            number = str(meta.get("number") or "").strip()
            if not number:
                continue
            incident_sources.add(f"incident:{number}")
            incident_id = f"incident:{number}"
            for item in self.support_graph.related(incident_id, depth=2):
                relation = str(item.get("relation") or "related_to")
                entity = str(item.get("entity") or "")
                key = (relation, entity)
                if not entity or key in seen_graph:
                    continue
                seen_graph.add(key)
                props = item.get("properties") or {}
                name = props.get("name", entity)
                etype = item.get("type", "entity")
                graph_lines.append(f"- ({relation}) {name} [{etype}]")

        graph_context = ""
        if graph_lines:
            graph_context = "### Collective support graph\n" + "\n".join(graph_lines[:18])

        package = SupportEvidencePackage(
            query=query,
            governed_candidates=governed.candidates if governed_allowed else [],
            governed_context=governed.to_context_string() if governed_allowed else "",
            incident_context=_incident_context_from_docs(incident_docs),
            graph_context=graph_context,
            incident_sources=incident_sources,
            governed_should_answer=governed_allowed,
            confidence_score=governed.confidence_score,
        )
        self._put_cached(cache_key, package)
        return package

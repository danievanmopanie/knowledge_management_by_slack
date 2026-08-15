"""Evidence assembly for collaborative Frontend Support.

Retrieval lanes:
1. Exact incident lookup -> deterministic case file + enriched incident knowledge.
2. Organisational pattern retrieval -> enriched symptom index selects cases, then
   deterministic aggregation produces repeated resolutions/actions/root causes.
3. Raw historical incident vectors -> fallback candidate locator, hydrated back
   into complete case files.
4. Governed knowledge -> articles, notes and approved documents.

Embeddings locate evidence. They never become the source of truth for incident
facts or organisational frequency claims.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from src.core.config import settings
from src.core.context import RequestContext
from src.knowledge.incident_casefile import (
    IncidentCaseFileStore,
    extract_incident_numbers,
    looks_like_exact_incident_lookup,
)
from src.knowledge.incident_rag import IncidentRAG
from src.knowledge.organisational_knowledge import OrganisationalKnowledgeRetriever
from src.knowledge.retrieval_models import RetrievalCandidate, RetrievalQuery
from src.knowledge.retriever import HybridRetriever
from src.knowledge.support_graph import SupportKnowledgeGraph

CONVERSATIONAL_LIMIT = 3
PROMPT_CONTEXT_MAX_CHARS = 22000
INCIDENT_CONTEXT_MAX_CHARS = 7600
EXACT_INCIDENT_CONTEXT_MAX_CHARS = 26000
EVIDENCE_CACHE_TTL_SECONDS = 300
EVIDENCE_CACHE_MAX_ENTRIES = 128


@dataclass
class SupportEvidencePackage:
    query: str
    governed_candidates: list[RetrievalCandidate] = field(default_factory=list)
    governed_context: str = ""
    organisational_context: str = ""
    incident_context: str = ""
    graph_context: str = ""
    incident_sources: set[str] = field(default_factory=set)
    governed_should_answer: bool = False
    confidence_score: float = 0.0
    exact_incident_numbers: tuple[str, ...] = ()
    exact_incident_context: str = ""
    exact_incident_missing: tuple[str, ...] = ()

    @property
    def exact_lookup_requested(self) -> bool:
        return bool(self.exact_incident_numbers)

    @property
    def has_evidence(self) -> bool:
        return bool(
            self.exact_incident_context
            or self.organisational_context
            or self.governed_context
            or self.incident_context
            or self.graph_context
        )

    def to_prompt_context(self, max_chars: int = PROMPT_CONTEXT_MAX_CHARS) -> str:
        parts: list[str] = []
        # For exact lookups the distilled enrichment comes first so a very long
        # journal can never crowd out the extracted resolution/action knowledge.
        if self.organisational_context:
            parts.extend([self.organisational_context, "---"])
        if self.exact_incident_context:
            parts.extend([self.exact_incident_context, "---"])
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


def _context_scope(context: RequestContext) -> tuple:
    """Scope cache entries so permission-sensitive governed evidence cannot leak."""
    return (
        context.channel_id or "",
        context.user_id or "",
        tuple(sorted(context.roles or ())),
    )


def _incident_context_from_casefiles(
    docs: list,
    casefiles: IncidentCaseFileStore,
    *,
    max_chars: int = INCIDENT_CONTEXT_MAX_CHARS,
) -> str:
    """Hydrate semantically-selected raw incident IDs into complete case evidence."""
    if not docs:
        return ""
    parts = [
        "### Raw historical case evidence",
        "Raw vector similarity selected these incident IDs; details come from the deterministic incident store.",
    ]
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata or {}
        number = str(meta.get("number") or "").strip().upper()
        case = casefiles.get(number) if number else None
        if case is not None:
            score = meta.get("score")
            relevance = float(score) if score is not None else None
            rendered = casefiles.render_similar(
                case,
                relevance=relevance,
                matched_fields=str(meta.get("matched_fields") or "").replace(",", ", "),
            )
            parts.extend([f"[{i}] {rendered}", ""])
            continue

        header = (
            f"[{i}] {number or '?'} | {meta.get('state', '')} "
            f"| group={meta.get('assignment_group', '')} | loc={meta.get('location', '')}"
        )
        parts.extend([header, doc.page_content.strip(), ""])

    text = "\n".join(parts).strip()
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[Raw incident case evidence truncated]"
    return text


class SupportEvidenceService:
    """Build one evidence package for a Frontend Support turn."""

    def __init__(
        self,
        retriever: HybridRetriever | None = None,
        incident_rag: IncidentRAG | None = None,
        support_graph: SupportKnowledgeGraph | None = None,
        casefiles: IncidentCaseFileStore | None = None,
        organisational: OrganisationalKnowledgeRetriever | None = None,
    ):
        self.retriever = retriever or HybridRetriever()
        self.incident_rag = incident_rag or IncidentRAG()
        # Rich support graph is intentionally separate from the fast temporal
        # graph. Exact raw case files still use the temporal graph below.
        self.support_graph = support_graph or SupportKnowledgeGraph()
        self.casefiles = casefiles or IncidentCaseFileStore(graph_store=self.incident_rag.graph_store)
        self.organisational = organisational or OrganisationalKnowledgeRetriever()
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

    def _build_exact_package(
        self,
        query: str,
        numbers: list[str],
    ) -> SupportEvidencePackage:
        raw_contexts: list[str] = []
        enriched_contexts: list[str] = []
        missing: list[str] = []
        sources: set[str] = set()
        for number in numbers[:3]:
            case = self.casefiles.get(number)
            enriched = self.organisational.exact_context(number)
            if case is None and not enriched:
                missing.append(number)
                continue
            if case is not None:
                raw_contexts.append(
                    self.casefiles.render_exact(case, max_chars=EXACT_INCIDENT_CONTEXT_MAX_CHARS)
                )
            if enriched:
                enriched_contexts.append(enriched)
            sources.add(f"incident:{number}")

        return SupportEvidencePackage(
            query=query,
            exact_incident_numbers=tuple(numbers[:3]),
            exact_incident_context="\n\n---\n\n".join(raw_contexts),
            organisational_context="\n\n---\n\n".join(enriched_contexts),
            exact_incident_missing=tuple(missing),
            incident_sources=sources,
        )

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

        exact_numbers = extract_incident_numbers(query)
        if exact_numbers and looks_like_exact_incident_lookup(query):
            package = self._build_exact_package(query, exact_numbers)
            self._put_cached(cache_key, package)
            return package

        retrieval_query = RetrievalQuery(text=query, context=context, limit=limit, graph_depth=1)

        # Governed retrieval, enriched organisational retrieval and raw incident
        # selection are independent. The organisational lane is primary for
        # repeated support knowledge; raw incidents remain useful fallback evidence.
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="frontend-evidence") as pool:
            governed_future = pool.submit(self.retriever.search, retrieval_query)
            organisational_future = pool.submit(
                self.organisational.collective_context,
                query,
                candidate_k=settings.knowledge_pattern_candidate_k,
                max_incidents=settings.knowledge_pattern_max_incidents,
            )
            incident_future = pool.submit(self.incident_rag.similar_incidents, query, limit)
            governed = governed_future.result()
            organisational_context = organisational_future.result()
            incident_docs = incident_future.result()

        incident_sources: set[str] = set()
        graph_lines: list[str] = []
        seen_graph: set[tuple[str, str]] = set()

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
            graph_context = "### Enriched support graph\n" + "\n".join(graph_lines[:24])

        package = SupportEvidencePackage(
            query=query,
            governed_candidates=governed.candidates,
            governed_context=governed.to_context_string() if governed.should_answer else "",
            organisational_context=organisational_context,
            incident_context=_incident_context_from_casefiles(incident_docs, self.casefiles),
            graph_context=graph_context,
            incident_sources=incident_sources,
            governed_should_answer=governed.should_answer,
            confidence_score=governed.confidence_score,
        )
        self._put_cached(cache_key, package)
        return package

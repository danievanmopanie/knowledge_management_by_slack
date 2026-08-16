"""Asynchronously turn ingested incident evidence into reusable support knowledge.

The fast ServiceNow worker intentionally stays deterministic and fast. This worker
runs behind it, extracts a richer support model with the local LLM, updates the
knowledge graph, builds a dedicated enriched-knowledge vector index and
materialises cross-incident pattern statistics.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

from src.core.config import settings  # noqa: E402
from src.knowledge.knowledge_trust import (  # noqa: E402
    index_trusted_items,
    rebuild_trusted_patterns,
)
from src.knowledge.organisational_knowledge import (  # noqa: E402
    EnrichmentCandidate,
    OrganisationalKnowledgeIndex,
    OrganisationalKnowledgeStore,
)
from src.knowledge.support_extraction import (  # noqa: E402
    SupportExtraction,
    SupportKnowledgeExtractor,
    extraction_model_key,
)

logger = logging.getLogger(__name__)
TERMINAL_STATES = {"resolved", "closed", "cancelled", "canceled"}
CANDIDATE_POOL_MULTIPLIER = 8
ProgressCallback = Callable[[dict], None]


@dataclass
class EnrichmentBatchResult:
    selected: int = 0
    enriched: int = 0
    failed: int = 0
    indexed_documents: int = 0
    patterns: int = 0
    remaining: int = 0
    elapsed_seconds: float = 0.0


def _emit(on_progress: ProgressCallback | None, **event) -> None:
    if on_progress is None:
        return
    try:
        on_progress(event)
    except Exception:
        logger.exception("Knowledge enrichment progress callback failed")


def _knowledge_value(candidate: EnrichmentCandidate) -> tuple[int, int, int, int, str]:
    """Rank pending records so useful resolved knowledge is built first."""
    inc = candidate.incident
    resolution_len = len((inc.resolution_notes or "").strip())
    work_len = len((inc.work_notes or "").strip())
    description_len = len((inc.description or "").strip())
    terminal = int((inc.state or "").strip().lower() in TERMINAL_STATES)
    return (
        terminal,
        int(resolution_len >= 40),
        int(work_len >= 120),
        int(description_len >= 80),
        inc.number or "",
    )


def _select_candidates(
    store: OrganisationalKnowledgeStore,
    *,
    limit: int,
    model_key: str,
) -> list[EnrichmentCandidate]:
    pool_limit = max(limit, limit * CANDIDATE_POOL_MULTIPLIER)
    pool = store.pending(limit=pool_limit, model=model_key)
    return sorted(
        pool,
        key=lambda candidate: (
            -_knowledge_value(candidate)[0],
            -_knowledge_value(candidate)[1],
            -_knowledge_value(candidate)[2],
            -_knowledge_value(candidate)[3],
            _knowledge_value(candidate)[4],
        ),
    )[:limit]


async def _extract_batch(
    candidates: list[EnrichmentCandidate],
    extractor: SupportKnowledgeExtractor,
    *,
    on_progress: ProgressCallback | None = None,
) -> list[tuple[EnrichmentCandidate, SupportExtraction | None, Exception | None]]:
    semaphore = asyncio.Semaphore(max(1, int(settings.support_extraction_concurrency)))

    async def one(candidate: EnrichmentCandidate):
        async with semaphore:
            number = candidate.incident.number
            _emit(on_progress, stage="extracting", event="started", incident=number)
            try:
                extraction = await extractor.extract(candidate.incident)
                _emit(on_progress, stage="extracting", event="completed", incident=number)
                return candidate, extraction, None
            except Exception as exc:
                logger.exception("Knowledge extraction failed for %s", number)
                _emit(on_progress, stage="extracting", event="failed", incident=number)
                return candidate, None, exc

    return await asyncio.gather(*(one(candidate) for candidate in candidates))


async def run_once(
    *,
    store: OrganisationalKnowledgeStore | None = None,
    extractor: SupportKnowledgeExtractor | None = None,
    index: OrganisationalKnowledgeIndex | None = None,
    batch_size: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> EnrichmentBatchResult:
    started = time.perf_counter()
    store = store or OrganisationalKnowledgeStore()
    extractor = extractor or SupportKnowledgeExtractor()
    index = index or OrganisationalKnowledgeIndex()
    model_key = extraction_model_key()
    limit = max(1, int(batch_size or settings.knowledge_enrichment_batch_size))
    candidates = _select_candidates(store, limit=limit, model_key=model_key)
    result = EnrichmentBatchResult(selected=len(candidates))
    pending_before = store.pending_count(model=model_key)
    _emit(
        on_progress,
        stage="selected",
        event="batch",
        selected=result.selected,
        pending_before=pending_before,
        model=model_key,
    )
    if not candidates:
        result.remaining = pending_before
        result.elapsed_seconds = time.perf_counter() - started
        _emit(on_progress, stage="complete", event="batch", result=result)
        return result

    logger.info(
        "Knowledge enrichment batch: selected=%d model='%s' concurrency=%d pending_before=%d",
        len(candidates),
        model_key,
        settings.support_extraction_concurrency,
        pending_before,
    )

    extracted = await _extract_batch(candidates, extractor, on_progress=on_progress)
    enriched_items = []
    _emit(on_progress, stage="persisting", event="stage", selected=result.selected)
    for candidate, extraction, error in extracted:
        if error is not None or extraction is None:
            result.failed += 1
            continue
        try:
            extractor.apply(candidate.incident, extraction, save=False)
            item = store.upsert(candidate, extraction, model=model_key)
            enriched_items.append(item)
            result.enriched += 1
            _emit(
                on_progress,
                stage="persisting",
                event="completed",
                incident=item.incident_number,
                enriched=result.enriched,
                failed=result.failed,
            )
            logger.info(
                "Enriched %s pattern=%r resolution=%r confidence=%.2f",
                item.incident_number,
                item.pattern_label,
                item.resolution_pattern,
                item.confidence,
            )
        except Exception:
            result.failed += 1
            _emit(
                on_progress,
                stage="persisting",
                event="failed",
                incident=candidate.incident.number,
                enriched=result.enriched,
                failed=result.failed,
            )
            logger.exception("Could not persist enriched knowledge for %s", candidate.incident.number)

    if enriched_items:
        _emit(on_progress, stage="indexing", event="stage", enriched=result.enriched)
        extractor.graph.save()
        index_result = index_trusted_items(index, enriched_items, model_key=model_key)
        result.indexed_documents = int(index_result.get("documents") or 0)
        result.patterns = rebuild_trusted_patterns(store, model_key=model_key)

    result.remaining = store.pending_count(model=model_key)
    result.elapsed_seconds = time.perf_counter() - started
    _emit(on_progress, stage="complete", event="batch", result=result)
    logger.info(
        "Knowledge enrichment complete: selected=%d enriched=%d failed=%d indexed_docs=%d "
        "patterns=%d remaining=%d seconds=%.2f",
        result.selected,
        result.enriched,
        result.failed,
        result.indexed_documents,
        result.patterns,
        result.remaining,
        result.elapsed_seconds,
    )
    return result


async def run_forever() -> None:
    store = OrganisationalKnowledgeStore()
    extractor = SupportKnowledgeExtractor()
    index = OrganisationalKnowledgeIndex()
    logger.info(
        "Starting Knowledge Enrichment worker model='%s' batch=%d concurrency=%d",
        extraction_model_key(),
        settings.knowledge_enrichment_batch_size,
        settings.support_extraction_concurrency,
    )
    while True:
        result = await run_once(store=store, extractor=extractor, index=index)
        if result.selected == 0:
            await asyncio.sleep(max(1.0, float(settings.knowledge_enrichment_poll_seconds)))
        else:
            await asyncio.sleep(0.1)


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(run_forever())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

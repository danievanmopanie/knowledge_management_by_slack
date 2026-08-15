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
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

from src.core.config import settings  # noqa: E402
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


@dataclass
class EnrichmentBatchResult:
    selected: int = 0
    enriched: int = 0
    failed: int = 0
    indexed_documents: int = 0
    patterns: int = 0
    remaining: int = 0
    elapsed_seconds: float = 0.0


async def _extract_batch(
    candidates: list[EnrichmentCandidate],
    extractor: SupportKnowledgeExtractor,
) -> list[tuple[EnrichmentCandidate, SupportExtraction | None, Exception | None]]:
    semaphore = asyncio.Semaphore(max(1, int(settings.support_extraction_concurrency)))

    async def one(candidate: EnrichmentCandidate):
        async with semaphore:
            try:
                extraction = await extractor.extract(candidate.incident)
                return candidate, extraction, None
            except Exception as exc:  # keep the batch moving; worker will retry later
                logger.exception("Knowledge extraction failed for %s", candidate.incident.number)
                return candidate, None, exc

    return await asyncio.gather(*(one(candidate) for candidate in candidates))


async def run_once(
    *,
    store: OrganisationalKnowledgeStore | None = None,
    extractor: SupportKnowledgeExtractor | None = None,
    index: OrganisationalKnowledgeIndex | None = None,
    batch_size: int | None = None,
) -> EnrichmentBatchResult:
    started = time.perf_counter()
    store = store or OrganisationalKnowledgeStore()
    extractor = extractor or SupportKnowledgeExtractor()
    index = index or OrganisationalKnowledgeIndex()
    model_key = extraction_model_key()
    limit = batch_size or settings.knowledge_enrichment_batch_size
    candidates = store.pending(limit=max(1, int(limit)), model=model_key)
    result = EnrichmentBatchResult(selected=len(candidates))
    if not candidates:
        result.remaining = store.pending_count(model=model_key)
        result.elapsed_seconds = time.perf_counter() - started
        return result

    logger.info(
        "Knowledge enrichment batch: selected=%d model='%s' concurrency=%d pending_before=%d",
        len(candidates),
        model_key,
        settings.support_extraction_concurrency,
        store.pending_count(model=model_key),
    )

    extracted = await _extract_batch(candidates, extractor)
    enriched_items = []
    for candidate, extraction, error in extracted:
        if error is not None or extraction is None:
            result.failed += 1
            continue
        try:
            # Graph writes are deliberately serial even though LLM extraction is
            # concurrent. GraphStore is an in-memory NetworkX object persisted once
            # at the end of the batch.
            extractor.apply(candidate.incident, extraction, save=False)
            item = store.upsert(candidate, extraction, model=model_key)
            enriched_items.append(item)
            result.enriched += 1
            logger.info(
                "Enriched %s pattern=%r resolution=%r confidence=%.2f",
                item.incident_number,
                item.pattern_label,
                item.resolution_pattern,
                item.confidence,
            )
        except Exception:
            result.failed += 1
            logger.exception("Could not persist enriched knowledge for %s", candidate.incident.number)

    if enriched_items:
        extractor.graph.save()
        index_result = index.upsert_many(enriched_items)
        result.indexed_documents = int(index_result.get("documents") or 0)
        result.patterns = store.rebuild_patterns()

    result.remaining = store.pending_count(model=model_key)
    result.elapsed_seconds = time.perf_counter() - started
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
            # Yield between batches so Slack-facing processes are never starved.
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

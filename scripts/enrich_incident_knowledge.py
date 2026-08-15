#!/usr/bin/env python3
"""Validate, backfill and inspect the enriched incident knowledge model."""

from __future__ import annotations

import argparse
import asyncio
import json

from src.core.config import settings
from src.knowledge.organisational_knowledge import (
    EnrichmentCandidate,
    OrganisationalKnowledgeIndex,
    OrganisationalKnowledgeRetriever,
    OrganisationalKnowledgeStore,
    _incident_from_record,
)
from src.knowledge.support_extraction import SupportKnowledgeExtractor
from src.worker.knowledge_enrichment_worker import run_once


def _candidate_for_number(
    store: OrganisationalKnowledgeStore,
    number: str,
) -> EnrichmentCandidate | None:
    with store._connect() as conn:
        row = conn.execute(
            """
            SELECT number,row_hash,record_json,source_file
            FROM incident_current WHERE UPPER(number)=?
            """,
            (number.upper(),),
        ).fetchone()
    if row is None:
        return None
    record = json.loads(row["record_json"] or "{}")
    incident = _incident_from_record(record)
    if not incident.number:
        incident.number = str(row["number"])
    return EnrichmentCandidate(
        incident=incident,
        row_hash=str(row["row_hash"] or ""),
        source_file=str(row["source_file"] or ""),
    )


def _status(store: OrganisationalKnowledgeStore) -> None:
    with store._connect() as conn:
        enriched = int(conn.execute("SELECT COUNT(*) FROM support_incident_knowledge").fetchone()[0])
        actions = int(conn.execute("SELECT COUNT(*) FROM support_knowledge_actions").fetchone()[0])
        patterns = int(conn.execute("SELECT COUNT(*) FROM support_patterns").fetchone()[0])
        total = int(conn.execute("SELECT COUNT(*) FROM incident_current").fetchone()[0])
    print(f"current incidents: {total:,}")
    print(f"enriched incidents: {enriched:,}")
    print(f"pending enrichment: {store.pending_count():,}")
    print(f"structured actions: {actions:,}")
    print(f"materialised patterns: {patterns:,}")
    try:
        vectors = OrganisationalKnowledgeIndex().vector_store.count()
        print(f"enriched knowledge vectors: {vectors:,}")
    except Exception as exc:
        print(f"enriched knowledge vectors: unavailable ({exc})")
    print(f"extraction model: {settings.support_extraction_model}")


async def _enrich_incident(number: str) -> int:
    store = OrganisationalKnowledgeStore()
    candidate = _candidate_for_number(store, number)
    if candidate is None:
        print(f"{number.upper()} is not present in incident_current")
        return 2

    extractor = SupportKnowledgeExtractor()
    print(f"Extracting rich knowledge for {candidate.incident.number} with {settings.support_extraction_model}...")
    extraction = await extractor.extract(candidate.incident, force=True)
    extractor.apply(candidate.incident, extraction, save=False)
    item = store.upsert(candidate, extraction)
    extractor.graph.save()
    index_result = OrganisationalKnowledgeIndex().upsert_many([item])
    patterns = store.rebuild_patterns()

    print("\nEXTRACTION")
    print(extraction.model_dump_json(indent=2))
    print("\nINDEX")
    print(index_result)
    print(f"patterns: {patterns}")
    print("\nFRONTEND SUPPORT EXACT-KNOWLEDGE CONTEXT")
    retriever = OrganisationalKnowledgeRetriever(store=store)
    print(retriever.exact_context(candidate.incident.number))
    return 0


async def _run_batches(limit: int, until_empty: bool) -> int:
    store = OrganisationalKnowledgeStore()
    extractor = SupportKnowledgeExtractor()
    index = OrganisationalKnowledgeIndex()
    total_enriched = 0
    total_failed = 0
    while True:
        result = await run_once(
            store=store,
            extractor=extractor,
            index=index,
            batch_size=limit,
        )
        total_enriched += result.enriched
        total_failed += result.failed
        print(
            f"batch selected={result.selected} enriched={result.enriched} failed={result.failed} "
            f"vectors={result.indexed_documents} patterns={result.patterns} "
            f"remaining={result.remaining} seconds={result.elapsed_seconds:.1f}"
        )
        if not until_empty or result.selected == 0:
            break
    print(f"total enriched this run: {total_enriched}; failures: {total_failed}")
    return 0 if total_failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Show current enrichment coverage")
    group.add_argument("--incident", help="Enrich and print one exact incident")
    group.add_argument("--batch", type=int, metavar="N", help="Process one enrichment batch of N incidents")
    group.add_argument("--all", action="store_true", help="Run batches until the current backfill is complete")
    args = parser.parse_args()

    if args.status:
        _status(OrganisationalKnowledgeStore())
        return 0
    if args.incident:
        return asyncio.run(_enrich_incident(args.incident))
    if args.batch:
        return asyncio.run(_run_batches(max(1, args.batch), False))
    return asyncio.run(_run_batches(settings.knowledge_enrichment_batch_size, True))


if __name__ == "__main__":
    raise SystemExit(main())

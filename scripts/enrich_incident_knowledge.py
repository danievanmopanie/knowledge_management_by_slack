#!/usr/bin/env python3
"""Validate, backfill and inspect the enriched incident knowledge model."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from src.core.config import settings
from src.knowledge.organisational_knowledge import (
    EnrichmentCandidate,
    OrganisationalKnowledgeIndex,
    OrganisationalKnowledgeRetriever,
    OrganisationalKnowledgeStore,
    _incident_from_record,
)
from src.knowledge.support_extraction import (
    SupportKnowledgeExtractor,
    extraction_model_key,
)
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
    model_key = extraction_model_key()
    with store._connect() as conn:
        enriched = int(conn.execute("SELECT COUNT(*) FROM support_incident_knowledge").fetchone()[0])
        current_schema_enriched = int(
            conn.execute(
                "SELECT COUNT(*) FROM support_incident_knowledge WHERE model=?",
                (model_key,),
            ).fetchone()[0]
        )
        actions = int(conn.execute("SELECT COUNT(*) FROM support_knowledge_actions").fetchone()[0])
        patterns = int(conn.execute("SELECT COUNT(*) FROM support_patterns").fetchone()[0])
        total = int(conn.execute("SELECT COUNT(*) FROM incident_current").fetchone()[0])
    print(f"current incidents: {total:,}")
    print(f"enriched incidents (any schema): {enriched:,}")
    print(f"enriched incidents (current schema): {current_schema_enriched:,}")
    print(f"pending enrichment: {store.pending_count(model=model_key):,}")
    print(f"structured actions: {actions:,}")
    print(f"materialised patterns: {patterns:,}")
    try:
        vectors = OrganisationalKnowledgeIndex().vector_store.count()
        print(f"enriched knowledge vectors: {vectors:,}")
    except Exception as exc:
        print(f"enriched knowledge vectors: unavailable ({exc})")
    print(f"extraction model: {settings.support_extraction_model}")
    print(f"enrichment schema key: {model_key}")


async def _enrich_incident(number: str) -> int:
    store = OrganisationalKnowledgeStore()
    candidate = _candidate_for_number(store, number)
    if candidate is None:
        print(f"{number.upper()} is not present in incident_current")
        return 2

    model_key = extraction_model_key()
    extractor = SupportKnowledgeExtractor()
    print(f"Extracting rich knowledge for {candidate.incident.number} with {model_key}...")
    extraction = await extractor.extract(candidate.incident, force=True)
    extractor.apply(candidate.incident, extraction, save=False)
    item = store.upsert(candidate, extraction, model=model_key)
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


def _duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class _BatchProgress:
    """Small dependency-free terminal progress view for enrichment backfills."""

    def __init__(self) -> None:
        self.started_at = time.perf_counter()
        self.selected = 0
        self.pending_before = 0
        self.completed = 0
        self.extraction_failed = 0
        self.enriched = 0
        self.persist_failed = 0
        self.current: set[str] = set()
        self.stage = "selecting"
        self.last_render_len = 0

    @staticmethod
    def _bar(done: int, total: int, width: int = 24) -> str:
        if total <= 0:
            return "░" * width
        filled = min(width, int(width * done / total))
        return "█" * filled + "░" * (width - filled)

    def _line(self) -> str:
        elapsed = time.perf_counter() - self.started_at
        done = min(self.completed, self.selected)
        average = elapsed / done if done else None
        eta = average * (self.selected - done) if average is not None else None
        current = ", ".join(sorted(self.current)) or "—"
        return (
            f"[{self._bar(done, self.selected)}] {done}/{self.selected} "
            f"| stage={self.stage} | current={current} "
            f"| enriched={self.enriched} failed={self.extraction_failed + self.persist_failed} "
            f"| elapsed={_duration(elapsed)} "
            f"| avg={average:.1f}s" if average is not None else
            f"[{self._bar(done, self.selected)}] {done}/{self.selected} "
            f"| stage={self.stage} | current={current} "
            f"| enriched={self.enriched} failed={self.extraction_failed + self.persist_failed} "
            f"| elapsed={_duration(elapsed)} | avg=--"
        ) + f" | ETA={_duration(eta)}"

    def render(self, *, newline: bool = False) -> None:
        line = self._line()
        if sys.stdout.isatty() and not newline:
            padding = " " * max(0, self.last_render_len - len(line))
            print("\r" + line + padding, end="", flush=True)
            self.last_render_len = len(line)
        else:
            if sys.stdout.isatty() and self.last_render_len:
                print()
                self.last_render_len = 0
            print(line, flush=True)

    def __call__(self, event: dict) -> None:
        stage = str(event.get("stage") or self.stage)
        kind = str(event.get("event") or "")
        self.stage = stage

        if stage == "selected":
            self.selected = int(event.get("selected") or 0)
            self.pending_before = int(event.get("pending_before") or 0)
            print("\nKnowledge enrichment", flush=True)
            print(f"Selected: {self.selected:,} | Pending before batch: {self.pending_before:,}", flush=True)
            print(f"Model: {event.get('model') or extraction_model_key()}", flush=True)
            self.render()
            return

        incident = str(event.get("incident") or "")
        if stage == "extracting" and kind == "started":
            if incident:
                self.current.add(incident)
            self.render()
            return

        if stage == "extracting" and kind in {"completed", "failed"}:
            if incident:
                self.current.discard(incident)
            self.completed += 1
            if kind == "failed":
                self.extraction_failed += 1
            self.render()
            return

        if stage == "persisting":
            if incident:
                self.current = {incident}
            self.enriched = int(event.get("enriched") or self.enriched)
            self.persist_failed = int(event.get("failed") or self.persist_failed)
            self.render()
            return

        if stage == "indexing":
            self.current.clear()
            self.render()
            return

        if stage == "complete":
            self.current.clear()
            result = event.get("result")
            if result is not None:
                self.enriched = int(result.enriched)
                self.persist_failed = int(result.failed)
                self.completed = max(self.completed, int(result.selected))
            self.render(newline=True)


async def _run_batches(limit: int, until_empty: bool) -> int:
    store = OrganisationalKnowledgeStore()
    extractor = SupportKnowledgeExtractor()
    index = OrganisationalKnowledgeIndex()
    total_enriched = 0
    total_failed = 0
    while True:
        progress = _BatchProgress()
        result = await run_once(
            store=store,
            extractor=extractor,
            index=index,
            batch_size=limit,
            on_progress=progress,
        )
        total_enriched += result.enriched
        total_failed += result.failed
        print(
            "\nBatch complete\n"
            f"  selected: {result.selected:,}\n"
            f"  enriched: {result.enriched:,}\n"
            f"  failed: {result.failed:,}\n"
            f"  enriched vectors written: {result.indexed_documents:,}\n"
            f"  materialised patterns: {result.patterns:,}\n"
            f"  remaining: {result.remaining:,}\n"
            f"  elapsed: {_duration(result.elapsed_seconds)}",
            flush=True,
        )
        if not until_empty or result.selected == 0:
            break
    print(f"\nTotal enriched this run: {total_enriched:,}; failures: {total_failed:,}")
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

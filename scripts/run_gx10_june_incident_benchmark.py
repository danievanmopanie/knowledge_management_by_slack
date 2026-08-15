#!/usr/bin/env python3
"""Benchmark the real June ServiceNow incident export, then optionally promote it live.

Default source on the GX10:
    ~/Downloads/incident (Jun).csv

Safety model:
- default: isolated benchmark only; live knowledge is untouched;
- --profile-only: parse/profile only;
- --ingest-live: archive the exact CSV into INCIDENTS_PATH and ingest it permanently.

June is treated as a historical/month-scoped import, not a complete current
snapshot, so missing-ticket detection is disabled. July and August should then
be ingested in chronological order using the same partial-snapshot semantics.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from src.core.config import settings
from src.knowledge.fast_incident_ingest import (
    FastIncidentEmbedder,
    FastTemporalIncidentIngestor,
    profile_incident_csv,
)
from src.knowledge.fast_temporal_graph import FastTemporalGraphBuilder
from src.knowledge.graphstore import GraphStore
from src.knowledge.temporal_incidents import TemporalIncidentStore
from src.knowledge.vectorstore import VectorStore

DEFAULT_JUNE = Path.home() / "Downloads" / "incident (Jun).csv"


def _isolated_ingestor(root: Path) -> FastTemporalIncidentIngestor:
    vector = VectorStore(
        path=root / "vectorstore",
        collection_name="incidents",
        embedding_purpose="incident",
    )
    return FastTemporalIncidentIngestor(
        temporal_store=TemporalIncidentStore(root / "platform.db"),
        embedder=FastIncidentEmbedder(
            vector,
            hash_path=root / "incident_content_hashes.json",
        ),
        graph_builder=FastTemporalGraphBuilder(GraphStore(root / "graph")),
    )


def _print_profile(path: Path):
    profile = profile_incident_csv(path)
    print("\n=== JUNE PROFILE ===")
    print(json.dumps(profile.__dict__, indent=2, sort_keys=True))
    return profile


def _print_result(label: str, result) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))


def _summary(profile, first, repeat=None) -> None:
    print("\n" + "=" * 58)
    print("CREATE KNOWLEDGE — JUNE BENCHMARK SUMMARY")
    print("=" * 58)
    print(f"Rows / unique incidents: {profile.rows:,} / {profile.unique_incidents:,}")
    print(f"Focused vector documents: {first.vector_documents:,}")
    print(f"Initial total: {first.total_seconds:.2f}s")
    print(f"Initial embedding: {first.embedding_seconds:.2f}s")
    print(f"Embedding throughput: {first.embedding_documents_per_second:.1f} docs/sec")
    print(f"Graph build: {first.graph_seconds:.2f}s")
    print(f"Temporal delta: {first.temporal_seconds:.2f}s")
    if repeat is not None:
        speedup = first.total_seconds / repeat.total_seconds if repeat.total_seconds > 0 else 0.0
        print(f"Repeat total: {repeat.total_seconds:.3f}s")
        print(f"Repeat unchanged: {repeat.unchanged:,}")
        print(f"Repeat vectors: {repeat.vector_documents:,}")
        print(f"Repeat speed-up: {speedup:.1f}x")
    print("=" * 58)


def _archive_live_source(source: Path) -> Path:
    settings.incidents_path.mkdir(parents=True, exist_ok=True)
    destination = settings.incidents_path / source.name
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark or permanently ingest the real June ServiceNow incident CSV."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_JUNE,
        help=f"June CSV (default: {DEFAULT_JUNE})",
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help="Profile the June CSV without writing vectors, graph or temporal state",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=2,
        help="Isolated benchmark passes; 2 validates the unchanged fast path (default: 2)",
    )
    parser.add_argument(
        "--ingest-live",
        action="store_true",
        help="PERMANENT: archive June into INCIDENTS_PATH and ingest it into live knowledge state",
    )
    args = parser.parse_args()

    source = args.csv.expanduser().resolve()
    if not source.exists():
        print(f"ERROR: June file not found: {source}", file=sys.stderr)
        print(
            "Expected ~/Downloads/incident (Jun).csv or pass another path with --csv.",
            file=sys.stderr,
        )
        return 2
    if source.suffix.lower() != ".csv":
        print(f"ERROR: expected a CSV file, got {source.name}", file=sys.stderr)
        return 2
    if args.passes < 1:
        print("ERROR: --passes must be at least 1", file=sys.stderr)
        return 2

    size_mb = source.stat().st_size / (1024 * 1024)
    print("=== GX10 REAL JUNE INCIDENT IMPORT ===")
    print(f"Source: {source}")
    print(f"Size:   {size_mb:.2f} MiB")
    print("Snapshot semantics: historical/month-scoped partial snapshot")

    profile = _print_profile(source)
    if profile.unique_incidents == 0:
        print("ERROR: no recognisable incidents found", file=sys.stderr)
        return 2
    if args.profile_only:
        return 0

    if args.ingest_live:
        destination = _archive_live_source(source)
        print("\n*** PERMANENT LIVE INGEST ***")
        print(f"Archived source: {destination}")
        result = FastTemporalIncidentIngestor().ingest_csv(
            destination,
            complete_snapshot=False,
        )
        _print_result("LIVE JUNE INGEST", result)
        _summary(profile, result)
        print("\nJune is now in the configured permanent temporal/vector/graph knowledge stores.")
        print("Ingest July next, then August, to preserve chronological snapshot order.")
        return 0

    print("\nState: ISOLATED benchmark — live knowledge will not be modified")
    with tempfile.TemporaryDirectory(prefix="create-knowledge-june-benchmark-") as temp:
        root = Path(temp)
        print(f"Temporary state: {root}")
        ingestor = _isolated_ingestor(root)
        results = []
        for pass_number in range(1, args.passes + 1):
            result = ingestor.ingest_csv(source, complete_snapshot=False)
            results.append(result)
            _print_result(f"ISOLATED JUNE PASS {pass_number}", result)
        _summary(profile, results[0], results[1] if len(results) > 1 else None)

    print("\nIsolated state removed; permanent knowledge was not modified.")
    print("If this benchmark is healthy, promote the exact same file with:")
    print("  python scripts/run_gx10_june_incident_benchmark.py --ingest-live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the 1,000-incident GX10 temporal-ingest benchmark.

Default sample:
    ~/Downloads/closed_incidents_sample_1000.csv

Useful modes:
- normal: uses the configured platform/vector/graph state;
- --isolated: uses temporary SQLite, Chroma, graph and hash state;
- --with-day2: after the Day-1 passes, ingest the generated synthetic Day-2 CSV.

All sample runs are partial snapshots so incidents outside the sample are never
marked missing.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from src.knowledge.fast_incident_ingest import (
    FastIncidentEmbedder,
    FastTemporalIncidentIngestor,
    profile_incident_csv,
)
from src.knowledge.fast_temporal_graph import FastTemporalGraphBuilder
from src.knowledge.graphstore import GraphStore
from src.knowledge.temporal_incidents import TemporalIncidentStore
from src.knowledge.vectorstore import VectorStore

DEFAULT_SAMPLE = Path.home() / "Downloads" / "closed_incidents_sample_1000.csv"
DEFAULT_DAY2 = Path.home() / "Downloads" / "closed_incidents_sample_1000_day2.csv"


def _print_profile(path: Path, label: str = "SAMPLE PROFILE") -> None:
    profile = profile_incident_csv(path)
    print(f"\n=== {label} ===")
    print(json.dumps(profile.__dict__, indent=2, sort_keys=True))


def _print_result(label: str, result) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))


def _isolated_ingestor(root: Path) -> FastTemporalIncidentIngestor:
    vector_path = root / "vectorstore"
    temporal_path = root / "platform.db"
    graph_path = root / "graph"
    hash_path = root / "incident_content_hashes.json"

    vector = VectorStore(
        path=vector_path,
        collection_name="incidents",
        embedding_purpose="incident",
    )
    return FastTemporalIncidentIngestor(
        temporal_store=TemporalIncidentStore(temporal_path),
        embedder=FastIncidentEmbedder(vector, hash_path=hash_path),
        graph_builder=FastTemporalGraphBuilder(GraphStore(graph_path)),
    )


def _run(
    *,
    ingestor: FastTemporalIncidentIngestor,
    day1: Path,
    passes: int,
    day2: Path | None,
) -> None:
    for pass_number in range(1, passes + 1):
        result = ingestor.ingest_csv(day1, complete_snapshot=False)
        _print_result(f"INGEST DAY 1 PASS {pass_number}", result)

    if day2 is not None:
        _print_profile(day2, "DAY 2 PROFILE")
        result = ingestor.ingest_csv(day2, complete_snapshot=False)
        _print_result("INGEST DAY 2 INCREMENTAL", result)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the fast temporal incident ingest path using the GX10 1,000-row sample."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_SAMPLE,
        help=f"Day-1 CSV to benchmark (default: {DEFAULT_SAMPLE})",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=2,
        help="Number of consecutive Day-1 passes (default: 2)",
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help="Validate and profile Day 1 without embedding or graph writes",
    )
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="Use throw-away SQLite/Chroma/graph/hash state; does not touch live knowledge state",
    )
    parser.add_argument(
        "--isolated-dir",
        type=Path,
        default=None,
        help="Use a persistent isolated state directory instead of a temporary one",
    )
    parser.add_argument(
        "--with-day2",
        action="store_true",
        help=f"Also ingest the synthetic Day-2 snapshot (default path: {DEFAULT_DAY2})",
    )
    parser.add_argument(
        "--day2-csv",
        type=Path,
        default=DEFAULT_DAY2,
        help=f"Synthetic Day-2 CSV (default: {DEFAULT_DAY2})",
    )
    args = parser.parse_args()

    day1 = args.csv.expanduser().resolve()
    if not day1.exists():
        print(f"ERROR: sample file not found: {day1}", file=sys.stderr)
        return 2
    if day1.suffix.lower() != ".csv":
        print(f"ERROR: expected a CSV file, got: {day1.name}", file=sys.stderr)
        return 2
    if args.passes < 1:
        print("ERROR: --passes must be at least 1", file=sys.stderr)
        return 2

    day2: Path | None = None
    if args.with_day2:
        day2 = args.day2_csv.expanduser().resolve()
        if not day2.exists():
            print(f"ERROR: Day-2 file not found: {day2}", file=sys.stderr)
            print(
                "Run: python scripts/generate_synthetic_day2_incidents.py",
                file=sys.stderr,
            )
            return 2

    size_mb = day1.stat().st_size / (1024 * 1024)
    print("=== GX10 FAST TEMPORAL INCIDENT BENCHMARK ===")
    print(f"Day 1:  {day1}")
    if day2:
        print(f"Day 2:  {day2}")
    print(f"Size:   {size_mb:.2f} MiB")
    print("Mode:   partial snapshot (missing detection disabled for this sample)")

    _print_profile(day1)
    if args.profile_only:
        return 0

    if args.isolated_dir is not None:
        root = args.isolated_dir.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        print(f"State:  ISOLATED persistent -> {root}")
        _run(ingestor=_isolated_ingestor(root), day1=day1, passes=args.passes, day2=day2)
    elif args.isolated:
        with tempfile.TemporaryDirectory(prefix="create-knowledge-benchmark-") as temp:
            root = Path(temp)
            print(f"State:  ISOLATED temporary -> {root}")
            _run(ingestor=_isolated_ingestor(root), day1=day1, passes=args.passes, day2=day2)
            print("\nIsolated benchmark state removed; live knowledge state was not modified.")
    else:
        print("State:  configured/live benchmark state")
        _run(ingestor=FastTemporalIncidentIngestor(), day1=day1, passes=args.passes, day2=day2)

    print("\n=== WHAT TO CHECK ===")
    print("Day 1 pass 1: vector_collection_total should equal vectors created in isolated mode.")
    if args.passes >= 2:
        print("Day 1 pass 2: unchanged should match unique incidents and vector_documents should be 0.")
    if day2:
        print("Day 2: expect 100 changed unique incidents: 40 metadata-only + 60 semantic changes.")
        print("Day 2 vector_metadata_only_incidents should be about 40; vector_incidents about 60.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

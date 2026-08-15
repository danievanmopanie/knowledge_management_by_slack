#!/usr/bin/env python3
"""Run the 1,000-incident GX10 temporal-ingest benchmark.

By default this picks up the sample placed in the GX10 Downloads folder:
    ~/Downloads/closed_incidents_sample_1000.csv

The sample is always treated as a partial snapshot so incidents outside this
sample are never marked missing. Two passes are run by default:
  1. initial/new-or-changed ingest;
  2. identical rerun to measure the unchanged fast path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.knowledge.fast_incident_ingest import (
    FastTemporalIncidentIngestor,
    profile_incident_csv,
)

DEFAULT_SAMPLE = Path.home() / "Downloads" / "closed_incidents_sample_1000.csv"


def _print_profile(path: Path) -> None:
    profile = profile_incident_csv(path)
    print("\n=== SAMPLE PROFILE ===")
    print(json.dumps(profile.__dict__, indent=2, sort_keys=True))


def _print_result(pass_number: int, result) -> None:
    print(f"\n=== INGEST PASS {pass_number} ===")
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the fast temporal incident ingest path using the GX10 1,000-row sample."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_SAMPLE,
        help=f"CSV to benchmark (default: {DEFAULT_SAMPLE})",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=2,
        help="Number of consecutive ingest passes to run (default: 2)",
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help="Validate and profile the CSV without embedding or graph writes",
    )
    args = parser.parse_args()

    path = args.csv.expanduser().resolve()
    if not path.exists():
        print(f"ERROR: sample file not found: {path}", file=sys.stderr)
        print(
            "Expected the sample at ~/Downloads/closed_incidents_sample_1000.csv "
            "or pass another path with --csv.",
            file=sys.stderr,
        )
        return 2
    if path.suffix.lower() != ".csv":
        print(f"ERROR: expected a CSV file, got: {path.name}", file=sys.stderr)
        return 2
    if args.passes < 1:
        print("ERROR: --passes must be at least 1", file=sys.stderr)
        return 2

    size_mb = path.stat().st_size / (1024 * 1024)
    print("=== GX10 FAST TEMPORAL INCIDENT BENCHMARK ===")
    print(f"Sample: {path}")
    print(f"Size:   {size_mb:.2f} MiB")
    print("Mode:   partial snapshot (missing detection disabled for this sample)")

    _print_profile(path)
    if args.profile_only:
        return 0

    ingestor = FastTemporalIncidentIngestor()
    for pass_number in range(1, args.passes + 1):
        result = ingestor.ingest_csv(path, complete_snapshot=False)
        _print_result(pass_number, result)

    if args.passes >= 2:
        print("\n=== WHAT TO CHECK ===")
        print("Pass 1: note total_seconds, embedding_seconds and embedding_documents_per_second.")
        print("Pass 2: unchanged should be close to the sample size and vector_documents should be 0.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

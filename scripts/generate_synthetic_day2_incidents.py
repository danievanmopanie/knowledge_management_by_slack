#!/usr/bin/env python3
"""Generate a deterministic synthetic Day-2 incident snapshot for GX10 benchmarks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.knowledge.synthetic_day2 import generate_day2_snapshot

DEFAULT_SOURCE = Path.home() / "Downloads" / "closed_incidents_sample_1000.csv"
DEFAULT_OUTPUT = Path.home() / "Downloads" / "closed_incidents_sample_1000_day2.csv"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a Day-2 snapshot with lifecycle-only and semantic incident changes."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--lifecycle", type=int, default=40, help="Lifecycle-only unique incidents")
    parser.add_argument("--work-notes", type=int, default=30, help="Work Notes semantic changes")
    parser.add_argument("--resolution", type=int, default=30, help="Resolution Notes semantic changes")
    parser.add_argument("--seed", type=int, default=20260815)
    parser.add_argument(
        "--day2-timestamp",
        default=None,
        help="Synthetic Updated timestamp; default is one day after the latest source timestamp",
    )
    args = parser.parse_args()

    result = generate_day2_snapshot(
        args.source,
        args.output,
        lifecycle_count=args.lifecycle,
        work_notes_count=args.work_notes,
        resolution_count=args.resolution,
        seed=args.seed,
        day2_timestamp=args.day2_timestamp,
    )

    print("=== SYNTHETIC DAY-2 SNAPSHOT CREATED ===")
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    print("\nExpected unique incident changes:")
    print(f"  lifecycle-only: {len(result.lifecycle_only)}  -> no embedding expected")
    print(f"  work notes:     {len(result.work_notes_changed)}  -> semantic embedding expected")
    print(f"  resolution:     {len(result.resolution_changed)}  -> semantic embedding expected")
    print(f"  total changed:  {result.changed_unique_incidents}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

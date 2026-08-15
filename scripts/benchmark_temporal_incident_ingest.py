#!/usr/bin/env python3
"""Benchmark the fast ServiceNow temporal ingest path on the GX10."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.knowledge.fast_incident_ingest import FastTemporalIncidentIngestor, profile_incident_csv


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path, help="ServiceNow Incident CSV export")
    parser.add_argument(
        "--partial-snapshot",
        action="store_true",
        help="Do not mark previously known open incidents as missing when absent from this file",
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help="Parse/profile the CSV without embedding or graph writes",
    )
    args = parser.parse_args()

    profile = profile_incident_csv(args.csv)
    print("PROFILE")
    print(json.dumps(profile.__dict__, indent=2, sort_keys=True))
    if args.profile_only:
        return 0

    result = FastTemporalIncidentIngestor().ingest_csv(
        args.csv,
        complete_snapshot=not args.partial_snapshot,
    )
    print("\nINGEST RESULT")
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

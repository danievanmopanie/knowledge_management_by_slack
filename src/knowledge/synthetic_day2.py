"""Deterministic synthetic Day-2 ServiceNow incident snapshots for benchmarks.

The generator preserves the source CSV shape and changes selected *incident
numbers*, not arbitrary rows. If an incident appears more than once in the
source export, every row for that incident receives the same Day-2 mutation so
last-row-wins snapshot processing remains deterministic.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import asdict, dataclass
from datetime import timedelta, timezone
from pathlib import Path

from src.reporting.incidents import load_incidents_from_csv, map_incident_headers

DEFAULT_SEED = 20260815
DEFAULT_LIFECYCLE_COUNT = 40
DEFAULT_WORK_NOTES_COUNT = 30
DEFAULT_RESOLUTION_COUNT = 30


@dataclass(frozen=True)
class SyntheticDay2Result:
    source_file: str
    output_file: str
    manifest_file: str
    seed: int
    source_rows: int
    unique_incidents: int
    changed_unique_incidents: int
    changed_rows: int
    lifecycle_only: list[str]
    work_notes_changed: list[str]
    resolution_changed: list[str]
    lifecycle_column: str
    updated_at_column: str
    day2_timestamp: str

    def as_dict(self) -> dict:
        return asdict(self)


def _derived_day2_timestamp(source: Path) -> str:
    incidents = load_incidents_from_csv(source)
    timestamps = [
        ts
        for inc in incidents
        for ts in (inc.updated_at, inc.resolved_at, inc.closed_at, inc.opened_at)
        if ts is not None
    ]
    if timestamps:
        latest = max(timestamps)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return (latest + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    return "2026-08-16 12:00:00"


def _append_marker(existing: str, marker: str) -> str:
    existing = (existing or "").strip()
    if marker in existing:
        return existing
    return f"{existing}\n{marker}".strip()


def generate_day2_snapshot(
    source: Path,
    output: Path,
    *,
    lifecycle_count: int = DEFAULT_LIFECYCLE_COUNT,
    work_notes_count: int = DEFAULT_WORK_NOTES_COUNT,
    resolution_count: int = DEFAULT_RESOLUTION_COUNT,
    seed: int = DEFAULT_SEED,
    day2_timestamp: str | None = None,
) -> SyntheticDay2Result:
    source = Path(source).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    if source == output:
        raise ValueError("Day-2 output must be different from the source CSV")
    if not source.exists():
        raise FileNotFoundError(source)

    with source.open(newline="", encoding="utf-8-sig", errors="ignore") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Source CSV has no headers")
        headers = list(reader.fieldnames)
        rows = [dict(row) for row in reader]

    mapping = map_incident_headers(headers, rows)
    number_col = mapping.get("number")
    work_notes_col = mapping.get("work_notes")
    resolution_col = mapping.get("resolution_notes")
    if not number_col:
        raise ValueError(
            "Source CSV does not contain a recognisable incident number column "
            "and no column contains ServiceNow INCxxxx values"
        )
    if work_notes_count and not work_notes_col:
        raise ValueError("Source CSV does not contain a recognisable Work Notes column")
    if resolution_count and not resolution_col:
        raise ValueError("Source CSV does not contain a recognisable Resolution Notes column")

    lifecycle_key = next(
        (key for key in ("assignment_group", "assigned_to", "state") if mapping.get(key)),
        None,
    )
    if lifecycle_count and not lifecycle_key:
        raise ValueError("Source CSV needs Assignment Group, Assigned To or State for lifecycle changes")
    lifecycle_col = mapping.get(lifecycle_key or "", "")
    updated_col = mapping.get("updated_at", "")

    unique_numbers = sorted(
        {
            str(row.get(number_col) or "").strip()
            for row in rows
            if str(row.get(number_col) or "").strip()
        }
    )
    total_changes = lifecycle_count + work_notes_count + resolution_count
    if total_changes > len(unique_numbers):
        raise ValueError(
            f"Requested {total_changes} changed incidents but source has only "
            f"{len(unique_numbers)} unique incidents"
        )

    rng = random.Random(seed)
    chosen = rng.sample(unique_numbers, total_changes)
    lifecycle = set(chosen[:lifecycle_count])
    work_notes = set(chosen[lifecycle_count : lifecycle_count + work_notes_count])
    resolution = set(chosen[lifecycle_count + work_notes_count :])
    changed = lifecycle | work_notes | resolution
    timestamp = day2_timestamp or _derived_day2_timestamp(source)

    changed_rows = 0
    for row in rows:
        number = str(row.get(number_col) or "").strip()
        if number not in changed:
            continue
        changed_rows += 1
        if updated_col:
            row[updated_col] = timestamp

        if number in lifecycle:
            current = str(row.get(lifecycle_col) or "").strip()
            if lifecycle_key == "state":
                row[lifecycle_col] = (
                    "On Hold" if current.lower() == "in progress" else "In Progress"
                )
            elif lifecycle_key == "assigned_to":
                row[lifecycle_col] = "Synthetic Day2 Technician"
            else:
                row[lifecycle_col] = "Synthetic Day2 Support"
        elif number in work_notes:
            row[work_notes_col] = _append_marker(
                str(row.get(work_notes_col) or ""),
                "[Synthetic Day 2] Additional troubleshooting performed; "
                "symptom reproduced and evidence captured.",
            )
        elif number in resolution:
            row[resolution_col] = _append_marker(
                str(row.get(resolution_col) or ""),
                "[Synthetic Day 2] Resolution evidence updated after validation "
                "of the successful fix.",
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    manifest = output.with_suffix(".manifest.json")
    result = SyntheticDay2Result(
        source_file=str(source),
        output_file=str(output),
        manifest_file=str(manifest),
        seed=seed,
        source_rows=len(rows),
        unique_incidents=len(unique_numbers),
        changed_unique_incidents=len(changed),
        changed_rows=changed_rows,
        lifecycle_only=sorted(lifecycle),
        work_notes_changed=sorted(work_notes),
        resolution_changed=sorted(resolution),
        lifecycle_column=lifecycle_col,
        updated_at_column=updated_col,
        day2_timestamp=timestamp,
    )
    manifest.write_text(json.dumps(result.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return result

"""Incident records used by daily/weekly Frontend Support reports.

Data can come from:
1. Uploaded CSV files in data/raw (or ingested knowledge)
2. A local JSON/CSV incident export drop-zone
3. Future live ServiceNow integration

Expected CSV columns (flexible matching):
  number, short_description, description, state, assignment_group,
  assigned_to, caller, location, opened_at, resolved_at, category, subcategory
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src.core.config import settings

logger = logging.getLogger(__name__)

# Common column name aliases from ServiceNow / ITSM exports
COLUMN_ALIASES = {
    "number": ["number", "incident", "incident_number", "ticket", "id"],
    "short_description": ["short_description", "short description", "summary", "title"],
    "description": ["description", "details", "work_notes"],
    "state": ["state", "status"],
    "assignment_group": ["assignment_group", "assignment group", "group", "assigned_group"],
    "assigned_to": ["assigned_to", "assigned to", "assignee", "technician"],
    "caller": ["caller", "caller_id", "requested_for", "user", "employee"],
    "location": ["location", "site", "building", "floor", "office"],
    "opened_at": ["opened_at", "opened", "created", "created_at", "sys_created_on"],
    "resolved_at": ["resolved_at", "resolved", "closed_at", "sys_updated_on"],
    "category": ["category", "type"],
    "subcategory": ["subcategory", "sub_category"],
}


@dataclass
class Incident:
    number: str
    short_description: str = ""
    description: str = ""
    state: str = ""
    assignment_group: str = ""
    assigned_to: str = ""
    caller: str = ""
    location: str = ""
    opened_at: datetime | None = None
    resolved_at: datetime | None = None
    category: str = ""
    subcategory: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _norm(s: str) -> str:
    return " ".join(s.strip().lower().replace("_", " ").split())


def _map_headers(headers: list[str]) -> dict[str, str]:
    """Map actual CSV headers to canonical field names."""
    normalized = {_norm(h): h for h in headers}
    mapping: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = _norm(alias)
            if key in normalized:
                mapping[canonical] = normalized[key]
                break
    return mapping


def _parse_dt(value: str | None) -> datetime | None:
    if not value or not str(value).strip():
        return None
    value = str(value).strip()
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(value.replace("+00:00", "").replace("Z", ""), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def load_incidents_from_csv(path: Path) -> list[Incident]:
    incidents: list[Incident] = []
    with path.open(newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []
        mapping = _map_headers(list(reader.fieldnames))
        if "number" not in mapping and "short_description" not in mapping:
            logger.warning("CSV %s does not look like an incident export – skipping", path)
            return []

        for row in reader:
            def get(field: str) -> str:
                col = mapping.get(field)
                return (row.get(col) or "").strip() if col else ""

            number = get("number") or get("short_description") or "unknown"
            incidents.append(
                Incident(
                    number=number,
                    short_description=get("short_description"),
                    description=get("description"),
                    state=get("state"),
                    assignment_group=get("assignment_group"),
                    assigned_to=get("assigned_to"),
                    caller=get("caller"),
                    location=get("location"),
                    opened_at=_parse_dt(get("opened_at")),
                    resolved_at=_parse_dt(get("resolved_at")),
                    category=get("category"),
                    subcategory=get("subcategory"),
                    raw=dict(row),
                )
            )
    return incidents


def load_all_incidents(
    search_dirs: Iterable[Path] | None = None,
) -> list[Incident]:
    """Load incidents from CSV files in raw docs / incidents drop zone."""
    dirs = list(search_dirs or [settings.raw_docs_path, Path("./data/incidents")])
    all_incidents: list[Incident] = []
    seen_numbers: set[str] = set()

    for directory in dirs:
        directory = Path(directory)
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.csv")):
            try:
                batch = load_incidents_from_csv(path)
                for inc in batch:
                    # Prefer latest occurrence of same number
                    if inc.number in seen_numbers:
                        continue
                    seen_numbers.add(inc.number)
                    all_incidents.append(inc)
            except Exception:
                logger.exception("Failed to load incidents from %s", path)

    return all_incidents


def filter_incidents(
    incidents: list[Incident],
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    location: str | None = None,
    open_only: bool = False,
) -> list[Incident]:
    results = []
    location_norm = location.lower().strip() if location else None

    for inc in incidents:
        if since and inc.opened_at and inc.opened_at < since:
            continue
        if until and inc.opened_at and inc.opened_at > until:
            continue
        if location_norm and location_norm not in (inc.location or "").lower():
            continue
        if open_only:
            state = (inc.state or "").lower()
            if state in {"resolved", "closed", "cancelled", "canceled"}:
                continue
        results.append(inc)

    return results


def recent_window(hours: int = 24) -> tuple[datetime, datetime]:
    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=hours)
    return since, until


def last_week_window() -> tuple[datetime, datetime]:
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=7)
    return since, until

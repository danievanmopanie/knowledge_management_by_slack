"""Incident records used by reports, temporal processing and Incident RAG.

Daily ServiceNow exports are snapshots: the same incident can appear in many files.
This module preserves those versions while exposing a deterministic current record.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src.core.config import settings

logger = logging.getLogger(__name__)

COLUMN_ALIASES = {
    "number": ["number", "incident", "incident_number", "ticket", "id"],
    "short_description": ["short_description", "short description", "summary", "title"],
    "description": ["description", "details", "issue description", "problem description"],
    "work_notes": ["work_notes", "work notes", "work_note", "internal notes", "activity"],
    "comments": ["comments", "additional_comments", "additional comments", "customer comments", "public comments", "comment"],
    "resolution_notes": ["resolution_notes", "resolution notes", "close_notes", "close notes", "resolve_notes", "resolution", "resolution_description", "closed notes"],
    "state": ["state", "status"],
    "priority": ["priority"],
    "assignment_group": ["assignment_group", "assignment group", "group", "assigned_group"],
    "assigned_to": ["assigned_to", "assigned to", "assignee", "technician"],
    "caller": ["caller", "caller_id", "requested_for", "user", "employee"],
    "location": ["location", "site", "building", "floor", "office"],
    "configuration_item": ["configuration_item", "configuration item", "cmdb_ci", "ci"],
    "opened_at": ["opened_at", "opened", "created", "created_at", "sys_created_on"],
    "updated_at": ["updated_at", "updated", "last_updated", "sys_updated_on"],
    "resolved_at": ["resolved_at", "resolved"],
    "closed_at": ["closed_at", "closed"],
    "made_sla": ["made_sla", "made sla"],
    "reopen_count": ["reopen_count", "reopen count"],
    "category": ["category", "type"],
    "subcategory": ["subcategory", "sub_category"],
}

_MIN_TIME = datetime.min.replace(tzinfo=timezone.utc)
_SNAPSHOT_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)")


@dataclass
class Incident:
    number: str
    short_description: str = ""
    description: str = ""
    work_notes: str = ""
    comments: str = ""
    resolution_notes: str = ""
    state: str = ""
    priority: str = ""
    assignment_group: str = ""
    assigned_to: str = ""
    caller: str = ""
    location: str = ""
    configuration_item: str = ""
    opened_at: datetime | None = None
    updated_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    made_sla: str = ""
    reopen_count: str = ""
    category: str = ""
    subcategory: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def free_text_fields(self) -> dict[str, str]:
        return {
            "short_description": self.short_description or "",
            "description": self.description or "",
            "work_notes": self.work_notes or "",
            "comments": self.comments or "",
            "resolution_notes": self.resolution_notes or "",
        }


@dataclass(frozen=True)
class IncidentVersion:
    incident: Incident
    source_path: Path
    snapshot_at: datetime | None = None


def _norm(s: str) -> str:
    return " ".join(s.strip().lower().replace("_", " ").split())


def _map_headers(headers: list[str]) -> dict[str, str]:
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


def _snapshot_time_from_path(path: Path) -> datetime | None:
    match = _SNAPSHOT_DATE_RE.search(path.name)
    if not match:
        return None
    try:
        year, month, day = (int(part) for part in match.groups())
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _version_sort_key(version: IncidentVersion) -> tuple[datetime, datetime, str]:
    incident = version.incident
    primary = incident.updated_at or version.snapshot_at or incident.resolved_at or incident.closed_at or incident.opened_at or _MIN_TIME
    secondary = version.snapshot_at or incident.updated_at or incident.resolved_at or incident.closed_at or incident.opened_at or _MIN_TIME
    return primary, secondary, str(version.source_path)


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
                    work_notes=get("work_notes"),
                    comments=get("comments"),
                    resolution_notes=get("resolution_notes"),
                    state=get("state"),
                    priority=get("priority"),
                    assignment_group=get("assignment_group"),
                    assigned_to=get("assigned_to"),
                    caller=get("caller"),
                    location=get("location"),
                    configuration_item=get("configuration_item"),
                    opened_at=_parse_dt(get("opened_at")),
                    updated_at=_parse_dt(get("updated_at")),
                    resolved_at=_parse_dt(get("resolved_at")),
                    closed_at=_parse_dt(get("closed_at")),
                    made_sla=get("made_sla"),
                    reopen_count=get("reopen_count"),
                    category=get("category"),
                    subcategory=get("subcategory"),
                    raw=dict(row),
                )
            )
    return incidents


def load_incident_versions(search_dirs: Iterable[Path] | None = None) -> dict[str, list[IncidentVersion]]:
    dirs = list(search_dirs or [settings.raw_docs_path, settings.incidents_path])
    versions: dict[str, list[IncidentVersion]] = {}
    for directory in dirs:
        directory = Path(directory)
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.csv")):
            try:
                snapshot_at = _snapshot_time_from_path(path)
                for incident in load_incidents_from_csv(path):
                    versions.setdefault(incident.number, []).append(
                        IncidentVersion(incident=incident, source_path=path, snapshot_at=snapshot_at)
                    )
            except Exception:
                logger.exception("Failed to load incidents from %s", path)
    for incident_versions in versions.values():
        incident_versions.sort(key=_version_sort_key)
    return versions


def load_all_incidents(search_dirs: Iterable[Path] | None = None) -> list[Incident]:
    versions = load_incident_versions(search_dirs=search_dirs)
    current = [incident_versions[-1].incident for incident_versions in versions.values()]
    return sorted(current, key=lambda incident: incident.number)


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
        if open_only and (inc.state or "").lower() in {"resolved", "closed", "cancelled", "canceled"}:
            continue
        results.append(inc)
    return results


def recent_window(hours: int = 24) -> tuple[datetime, datetime]:
    until = datetime.now(timezone.utc)
    return until - timedelta(hours=hours), until


def last_week_window() -> tuple[datetime, datetime]:
    until = datetime.now(timezone.utc)
    return until - timedelta(days=7), until

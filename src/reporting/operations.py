"""Operational incident intelligence shared by morning and afternoon support pulses."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.reporting.incidents import Incident, load_all_incidents

CLOSED_STATES = {"resolved", "closed", "cancelled", "canceled"}


@dataclass(frozen=True)
class OperationsSnapshot:
    generated_at: datetime
    all_incidents: tuple[Incident, ...]
    open_incidents: tuple[Incident, ...]
    recent_activity: tuple[Incident, ...]
    aging_incidents: tuple[Incident, ...]
    stale_incidents: tuple[Incident, ...]


def _is_open(incident: Incident) -> bool:
    return (incident.state or "").strip().lower() not in CLOSED_STATES


def build_snapshot(
    *,
    window_hours: int,
    aging_days: int,
    stale_hours: int,
    now: datetime | None = None,
) -> OperationsSnapshot:
    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)

    all_incidents = tuple(load_all_incidents())
    open_incidents = tuple(i for i in all_incidents if _is_open(i))
    recent_cutoff = generated_at - timedelta(hours=window_hours)
    aging_cutoff = generated_at - timedelta(days=aging_days)
    stale_cutoff = generated_at - timedelta(hours=stale_hours)

    recent_activity = tuple(
        i
        for i in all_incidents
        if (i.updated_at or i.opened_at) and (i.updated_at or i.opened_at) >= recent_cutoff
    )
    aging_incidents = tuple(
        i for i in open_incidents if i.opened_at and i.opened_at <= aging_cutoff
    )
    stale_incidents = tuple(
        i
        for i in open_incidents
        if (i.updated_at or i.opened_at) and (i.updated_at or i.opened_at) <= stale_cutoff
    )

    return OperationsSnapshot(
        generated_at=generated_at,
        all_incidents=all_incidents,
        open_incidents=open_incidents,
        recent_activity=recent_activity,
        aging_incidents=aging_incidents,
        stale_incidents=stale_incidents,
    )


def _top(counter: Counter, limit: int = 8) -> list[str]:
    return [f"- {name}: {count}" for name, count in counter.most_common(limit)] or ["- none"]


def _incident_line(incident: Incident, now: datetime) -> str:
    age = "age unknown"
    if incident.opened_at:
        delta = now - incident.opened_at
        age = f"{max(delta.total_seconds(), 0) / 86400:.1f}d old"
    last_update = "update unknown"
    updated = incident.updated_at or incident.opened_at
    if updated:
        delta = now - updated
        hours = max(delta.total_seconds(), 0) / 3600
        last_update = f"last touched {hours:.0f}h ago"
    return (
        f"- {incident.number}: {incident.short_description or '(no summary)'} "
        f"[{incident.assignment_group or 'unassigned'} | {incident.location or 'no location'} | "
        f"{incident.state or 'state?'} | {age} | {last_update}]"
    )


def render_operations_digest(snapshot: OperationsSnapshot, *, sample_limit: int = 10) -> str:
    """Render deterministic operational facts for LLM synthesis without inventing metrics."""
    now = snapshot.generated_at
    open_groups = Counter(i.assignment_group or "Unassigned" for i in snapshot.open_incidents)
    open_locations = Counter(i.location or "Unknown location" for i in snapshot.open_incidents)
    recent_groups = Counter(i.assignment_group or "Unassigned" for i in snapshot.recent_activity)
    recent_locations = Counter(i.location or "Unknown location" for i in snapshot.recent_activity)
    categories = Counter(
        i.category or i.subcategory or i.short_description[:50] or "Other"
        for i in snapshot.recent_activity
    )

    lines = [
        "### Operational picture",
        f"Current unique incidents in data: {len(snapshot.all_incidents)}",
        f"Currently open: {len(snapshot.open_incidents)}",
        f"Recent activity in window: {len(snapshot.recent_activity)}",
        f"Aging open incidents: {len(snapshot.aging_incidents)}",
        f"Stale open incidents: {len(snapshot.stale_incidents)}",
        "",
        "Open work by assignment group:",
        *_top(open_groups),
        "",
        "Open work by location:",
        *_top(open_locations),
        "",
        "Recent activity by assignment group:",
        *_top(recent_groups),
        "",
        "Recent activity by location:",
        *_top(recent_locations),
        "",
        "Recent themes/categories:",
        *_top(categories),
    ]

    if snapshot.aging_incidents:
        lines.extend(["", "Oldest / aging work requiring attention:"])
        oldest = sorted(
            snapshot.aging_incidents,
            key=lambda i: i.opened_at or now,
        )[:sample_limit]
        lines.extend(_incident_line(i, now) for i in oldest)

    if snapshot.stale_incidents:
        lines.extend(["", "Stale open work (no recent touch):"])
        stale = sorted(
            snapshot.stale_incidents,
            key=lambda i: i.updated_at or i.opened_at or now,
        )[:sample_limit]
        lines.extend(_incident_line(i, now) for i in stale)

    if snapshot.recent_activity:
        lines.extend(["", "Sample recent activity:"])
        recent = sorted(
            snapshot.recent_activity,
            key=lambda i: i.updated_at or i.opened_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:sample_limit]
        lines.extend(_incident_line(i, now) for i in recent)

    return "\n".join(lines)

"""Tests for morning/afternoon operational support intelligence."""

from datetime import datetime, timedelta, timezone

from src.reporting.incidents import Incident
from src.reporting.operations import build_snapshot, render_operations_digest


def test_snapshot_separates_open_recent_aging_and_stale(monkeypatch):
    now = datetime(2026, 8, 14, 14, 0, tzinfo=timezone.utc)
    incidents = [
        Incident(
            number="INC1",
            short_description="Old VPN issue",
            state="In Progress",
            assignment_group="Group A",
            location="Site A",
            opened_at=now - timedelta(days=5),
            updated_at=now - timedelta(hours=30),
        ),
        Incident(
            number="INC2",
            short_description="Fresh Outlook issue",
            state="New",
            assignment_group="Group A",
            location="Site B",
            opened_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=1),
        ),
        Incident(
            number="INC3",
            short_description="Resolved printer issue",
            state="Resolved",
            assignment_group="Group B",
            location="Site B",
            opened_at=now - timedelta(days=4),
            updated_at=now - timedelta(hours=1),
        ),
    ]
    monkeypatch.setattr("src.reporting.operations.load_all_incidents", lambda: incidents)

    snapshot = build_snapshot(
        window_hours=10,
        aging_days=3,
        stale_hours=24,
        now=now,
    )

    assert {i.number for i in snapshot.open_incidents} == {"INC1", "INC2"}
    assert {i.number for i in snapshot.recent_activity} == {"INC2", "INC3"}
    assert {i.number for i in snapshot.aging_incidents} == {"INC1"}
    assert {i.number for i in snapshot.stale_incidents} == {"INC1"}

    digest = render_operations_digest(snapshot)
    assert "Currently open: 2" in digest
    assert "Aging open incidents: 1" in digest
    assert "Stale open incidents: 1" in digest
    assert "Group A: 2" in digest
    assert "Site B" in digest

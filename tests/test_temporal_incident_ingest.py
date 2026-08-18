from datetime import datetime, timezone

from src.knowledge.temporal_incidents import TemporalIncidentStore
from src.reporting.incidents import Incident


def _inc(number="INC1", state="New", group="Service Desk", updated="2026-08-15T08:00:00+00:00"):
    return Incident(
        number=number,
        short_description="App issue",
        state=state,
        assignment_group=group,
        opened_at=datetime(2026, 8, 15, 7, 0, tzinfo=timezone.utc),
        updated_at=datetime.fromisoformat(updated),
    )


def test_temporal_store_writes_only_changed_versions_and_dwell(tmp_path):
    store = TemporalIncidentStore(tmp_path / "platform.db")

    first = store.ingest_snapshot(
        [_inc()], source_file="incidents_2026-08-15.csv", observed_at="2026-08-15T08:05:00+00:00"
    )
    assert first.new == 1
    assert first.changed == 0
    assert first.versions_written == 1

    second = store.ingest_snapshot(
        [_inc(state="In Progress", group="Desktop", updated="2026-08-15T09:00:00+00:00")],
        source_file="incidents_2026-08-15_0900.csv",
        observed_at="2026-08-15T09:05:00+00:00",
    )
    assert second.new == 0
    assert second.changed == 1
    assert second.transitions_written == 2
    assert second.dwell_rows_written == 1

    third = store.ingest_snapshot(
        [_inc(state="In Progress", group="Desktop", updated="2026-08-15T09:00:00+00:00")],
        source_file="incidents_2026-08-15_1000.csv",
        observed_at="2026-08-15T10:05:00+00:00",
    )
    assert third.unchanged == 1
    assert third.versions_written == 0
    assert third.transitions_written == 0


def test_complete_snapshot_flags_missing_open_incidents(tmp_path):
    store = TemporalIncidentStore(tmp_path / "platform.db")
    store.ingest_snapshot(
        [_inc("INC1"), _inc("INC2")],
        source_file="day1.csv",
        observed_at="2026-08-15T08:00:00+00:00",
    )

    result = store.ingest_snapshot(
        [_inc("INC1")],
        source_file="day2.csv",
        observed_at="2026-08-16T08:00:00+00:00",
        complete_snapshot=True,
    )

    assert result.missing == 1

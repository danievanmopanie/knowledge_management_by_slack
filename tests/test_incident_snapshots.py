"""Regression tests for ServiceNow daily snapshot canonicalisation."""

from pathlib import Path

from src.reporting.incidents import load_all_incidents, load_incident_versions


def _write_snapshot(path: Path, state: str, updated: str = "") -> None:
    path.write_text(
        "number,short_description,state,sys_created_on,sys_updated_on\n"
        f"INC001,VPN disconnects,{state},2026-08-01 08:00:00,{updated}\n",
        encoding="utf-8",
    )


def test_latest_updated_record_wins_regardless_of_directory_order(tmp_path: Path) -> None:
    older_dir = tmp_path / "older"
    newer_dir = tmp_path / "newer"
    older_dir.mkdir()
    newer_dir.mkdir()

    _write_snapshot(
        older_dir / "incidents_2026-08-01.csv",
        "New",
        "2026-08-01 08:00:00",
    )
    _write_snapshot(
        newer_dir / "incidents_2026-08-03.csv",
        "Resolved",
        "2026-08-03 12:00:00",
    )

    current = load_all_incidents(search_dirs=[newer_dir, older_dir])

    assert len(current) == 1
    assert current[0].number == "INC001"
    assert current[0].state == "Resolved"
    assert current[0].updated_at is not None
    assert current[0].resolved_at is None


def test_filename_snapshot_date_is_fallback_when_update_timestamp_missing(
    tmp_path: Path,
) -> None:
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()

    _write_snapshot(snapshots / "incidents_2026-08-01.csv", "New")
    _write_snapshot(snapshots / "incidents_2026-08-02.csv", "In Progress")
    _write_snapshot(snapshots / "incidents_2026-08-03.csv", "Resolved")

    current = load_all_incidents(search_dirs=[snapshots])
    versions = load_incident_versions(search_dirs=[snapshots])

    assert current[0].state == "Resolved"
    assert [version.incident.state for version in versions["INC001"]] == [
        "New",
        "In Progress",
        "Resolved",
    ]


def test_historical_versions_remain_available(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()

    _write_snapshot(
        snapshots / "incidents_2026-08-01.csv",
        "New",
        "2026-08-01 08:00:00",
    )
    _write_snapshot(
        snapshots / "incidents_2026-08-02.csv",
        "In Progress",
        "2026-08-02 09:00:00",
    )
    _write_snapshot(
        snapshots / "incidents_2026-08-03.csv",
        "Resolved",
        "2026-08-03 10:00:00",
    )

    versions = load_incident_versions(search_dirs=[snapshots])

    assert len(versions["INC001"]) == 3
    assert versions["INC001"][-1].incident.state == "Resolved"

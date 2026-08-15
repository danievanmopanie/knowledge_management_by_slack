"""Fast temporal incident snapshot processing backed by SQLite.

This is the lightweight analogue of the temporal spine in the standalone ITSM
Temporal Graph RAG project.  It deliberately performs no LLM work: ServiceNow
snapshot rows are compared deterministically and only changes are persisted.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.core.config import settings
from src.reporting.incidents import Incident

TERMINAL_STATES = {"resolved", "closed", "cancelled", "canceled"}
TRACKED_FIELDS = (
    "state",
    "priority",
    "assignment_group",
    "assigned_to",
    "category",
    "subcategory",
    "location",
    "configuration_item",
)


def _iso(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value or "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(inc: Incident) -> dict:
    return {
        "number": inc.number,
        "short_description": inc.short_description or "",
        "description": inc.description or "",
        "work_notes": inc.work_notes or "",
        "comments": inc.comments or "",
        "resolution_notes": inc.resolution_notes or "",
        "state": inc.state or "",
        "priority": getattr(inc, "priority", "") or "",
        "assignment_group": inc.assignment_group or "",
        "assigned_to": inc.assigned_to or "",
        "caller": inc.caller or "",
        "location": inc.location or "",
        "configuration_item": getattr(inc, "configuration_item", "") or "",
        "opened_at": _iso(inc.opened_at),
        "updated_at": _iso(inc.updated_at),
        "resolved_at": _iso(inc.resolved_at),
        "closed_at": _iso(getattr(inc, "closed_at", None)),
        "category": inc.category or "",
        "subcategory": inc.subcategory or "",
    }


def _row_hash(record: dict) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _seconds_between(start: str, end: str) -> int | None:
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((b - a).total_seconds()))


@dataclass
class TemporalIngestResult:
    run_id: str
    source_file: str
    observed_at: str
    rows: int = 0
    unique_incidents: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    missing: int = 0
    versions_written: int = 0
    transitions_written: int = 0
    dwell_rows_written: int = 0
    elapsed_seconds: float = 0.0
    changed_incidents: list[Incident] = field(default_factory=list, repr=False)


class TemporalIncidentStore:
    """Persist current state and changed temporal facts without reprocessing history."""

    def __init__(self, path: Path | None = None):
        self.path = path or settings.platform_db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS incident_current (
                    number TEXT PRIMARY KEY,
                    row_hash TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    assignment_group_entered_at TEXT,
                    latest_seen_at TEXT NOT NULL,
                    source_file TEXT,
                    is_missing INTEGER NOT NULL DEFAULT 0,
                    missing_since TEXT
                );
                CREATE TABLE IF NOT EXISTS incident_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    number TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    source_file TEXT,
                    row_hash TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_incident_versions_number
                    ON incident_versions(number, observed_at);
                CREATE TABLE IF NOT EXISTS incident_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    number TEXT NOT NULL,
                    field TEXT NOT NULL,
                    from_value TEXT,
                    to_value TEXT,
                    event_time TEXT,
                    observed_at TEXT NOT NULL,
                    source_file TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_incident_transitions_number
                    ON incident_transitions(number, event_time);
                CREATE TABLE IF NOT EXISTS assignment_dwell (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    number TEXT NOT NULL,
                    assignment_group TEXT NOT NULL,
                    entered_at TEXT NOT NULL,
                    left_at TEXT NOT NULL,
                    dwell_seconds INTEGER,
                    next_group TEXT,
                    state_at_exit TEXT,
                    resolved_here INTEGER NOT NULL DEFAULT 0,
                    source_file TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_assignment_dwell_number
                    ON assignment_dwell(number, entered_at);
                CREATE TABLE IF NOT EXISTS incident_ingest_runs (
                    run_id TEXT PRIMARY KEY,
                    source_file TEXT,
                    observed_at TEXT NOT NULL,
                    rows INTEGER,
                    unique_incidents INTEGER,
                    new_count INTEGER,
                    changed_count INTEGER,
                    unchanged_count INTEGER,
                    missing_count INTEGER,
                    versions_written INTEGER,
                    transitions_written INTEGER,
                    dwell_rows_written INTEGER,
                    elapsed_seconds REAL
                );
                """
            )

    def ingest_snapshot(
        self,
        incidents: Iterable[Incident],
        *,
        source_file: str,
        observed_at: datetime | str | None = None,
        complete_snapshot: bool = True,
    ) -> TemporalIngestResult:
        started = time.perf_counter()
        observed = _iso(observed_at) or _now_iso()
        run_id = f"ING-{uuid.uuid4().hex[:10].upper()}"
        incident_list = list(incidents)
        # Last row wins when an export accidentally contains duplicate numbers.
        unique = {inc.number: inc for inc in incident_list if inc.number}
        result = TemporalIngestResult(
            run_id=run_id,
            source_file=source_file,
            observed_at=observed,
            rows=len(incident_list),
            unique_incidents=len(unique),
        )

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current_rows = {
                row["number"]: row
                for row in conn.execute("SELECT * FROM incident_current").fetchall()
            }

            for number, inc in unique.items():
                record = _record(inc)
                digest = _row_hash(record)
                previous = current_rows.get(number)
                event_time = record.get("updated_at") or observed

                if previous is None:
                    entered_at = record.get("opened_at") or event_time
                    conn.execute(
                        "INSERT INTO incident_versions(number,observed_at,source_file,row_hash,record_json) VALUES(?,?,?,?,?)",
                        (number, observed, source_file, digest, json.dumps(record, ensure_ascii=False)),
                    )
                    conn.execute(
                        "INSERT INTO incident_current(number,row_hash,record_json,assignment_group_entered_at,latest_seen_at,source_file,is_missing,missing_since) VALUES(?,?,?,?,?,?,0,NULL)",
                        (number, digest, json.dumps(record, ensure_ascii=False), entered_at, observed, source_file),
                    )
                    result.new += 1
                    result.versions_written += 1
                    result.changed_incidents.append(inc)
                    continue

                if previous["row_hash"] == digest:
                    conn.execute(
                        "UPDATE incident_current SET latest_seen_at=?, source_file=?, is_missing=0, missing_since=NULL WHERE number=?",
                        (observed, source_file, number),
                    )
                    result.unchanged += 1
                    continue

                old = json.loads(previous["record_json"])
                old_group = str(old.get("assignment_group") or "")
                new_group = str(record.get("assignment_group") or "")
                group_entered = previous["assignment_group_entered_at"] or old.get("updated_at") or old.get("opened_at") or observed

                for field_name in TRACKED_FIELDS:
                    before = str(old.get(field_name) or "")
                    after = str(record.get(field_name) or "")
                    if before == after:
                        continue
                    conn.execute(
                        "INSERT INTO incident_transitions(number,field,from_value,to_value,event_time,observed_at,source_file) VALUES(?,?,?,?,?,?,?)",
                        (number, field_name, before, after, event_time, observed, source_file),
                    )
                    result.transitions_written += 1

                if old_group and old_group != new_group:
                    dwell = _seconds_between(group_entered, event_time)
                    conn.execute(
                        "INSERT INTO assignment_dwell(number,assignment_group,entered_at,left_at,dwell_seconds,next_group,state_at_exit,resolved_here,source_file) VALUES(?,?,?,?,?,?,?,?,?)",
                        (number, old_group, group_entered, event_time, dwell, new_group, old.get("state") or "", 0, source_file),
                    )
                    result.dwell_rows_written += 1
                    group_entered = event_time

                old_state = str(old.get("state") or "").lower()
                new_state = str(record.get("state") or "").lower()
                if new_state in TERMINAL_STATES and old_state not in TERMINAL_STATES and new_group and old_group == new_group:
                    left_at = record.get("resolved_at") or record.get("closed_at") or event_time
                    dwell = _seconds_between(group_entered, left_at)
                    if dwell is not None:
                        conn.execute(
                            "INSERT INTO assignment_dwell(number,assignment_group,entered_at,left_at,dwell_seconds,next_group,state_at_exit,resolved_here,source_file) VALUES(?,?,?,?,?,?,?,?,?)",
                            (number, new_group, group_entered, left_at, dwell, "", record.get("state") or "", 1, source_file),
                        )
                        result.dwell_rows_written += 1

                conn.execute(
                    "INSERT INTO incident_versions(number,observed_at,source_file,row_hash,record_json) VALUES(?,?,?,?,?)",
                    (number, observed, source_file, digest, json.dumps(record, ensure_ascii=False)),
                )
                conn.execute(
                    "UPDATE incident_current SET row_hash=?,record_json=?,assignment_group_entered_at=?,latest_seen_at=?,source_file=?,is_missing=0,missing_since=NULL WHERE number=?",
                    (digest, json.dumps(record, ensure_ascii=False), group_entered, observed, source_file, number),
                )
                result.changed += 1
                result.versions_written += 1
                result.changed_incidents.append(inc)

            if complete_snapshot:
                seen = set(unique)
                for number, row in current_rows.items():
                    if number in seen:
                        continue
                    old = json.loads(row["record_json"])
                    if str(old.get("state") or "").lower() in TERMINAL_STATES:
                        continue
                    if not int(row["is_missing"] or 0):
                        conn.execute(
                            "UPDATE incident_current SET is_missing=1, missing_since=? WHERE number=?",
                            (observed, number),
                        )
                        result.missing += 1

            result.elapsed_seconds = time.perf_counter() - started
            conn.execute(
                "INSERT INTO incident_ingest_runs(run_id,source_file,observed_at,rows,unique_incidents,new_count,changed_count,unchanged_count,missing_count,versions_written,transitions_written,dwell_rows_written,elapsed_seconds) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    result.run_id,
                    source_file,
                    observed,
                    result.rows,
                    result.unique_incidents,
                    result.new,
                    result.changed,
                    result.unchanged,
                    result.missing,
                    result.versions_written,
                    result.transitions_written,
                    result.dwell_rows_written,
                    result.elapsed_seconds,
                ),
            )

        return result

    def recent_runs(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM incident_ingest_runs ORDER BY observed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

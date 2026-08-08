"""SQLite-backed store for work items, roles and the routines log.

This is the single source of truth for #work-management. It is used both by
the LangGraph nodes (src/agents/work_management/) while handling live Slack
traffic, and by the Routine Keeper's scheduled scripts. A short-lived
connection is opened per call rather than held open, which keeps concurrent
access from the bot process and the systemd-triggered scripts safe without
needing a connection pool for this workload.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from src.work_management.models import (
    ExecutionStatus,
    Role,
    RoutineLogEntry,
    RoutineName,
    Urgency,
    WeeklyRollup,
    WorkItem,
    WorkItemStatus,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    role_name       TEXT PRIMARY KEY,
    primary_person  TEXT NOT NULL,
    backup_person   TEXT,
    slack_user_id   TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS work_items (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    description           TEXT,
    requested_by          TEXT NOT NULL,
    date_requested        TEXT NOT NULL,

    urgency               TEXT NOT NULL DEFAULT 'Routine',
    status                TEXT NOT NULL DEFAULT 'Submitted',

    approved_by           TEXT,
    date_approved         TEXT,
    rejection_reason      TEXT,

    plan                  TEXT,

    resources             TEXT,
    resources_confirmed   INTEGER DEFAULT 0,

    scheduled_day         TEXT,
    assigned_to           TEXT,

    locked                INTEGER DEFAULT 0,
    locked_at             TEXT,

    execution_status      TEXT DEFAULT 'Not started',
    execution_started_at  TEXT,
    execution_done_at     TEXT,
    blocker_note          TEXT,

    follow_on_of          TEXT REFERENCES work_items(id),
    notes                 TEXT,

    slack_channel         TEXT,
    slack_ts              TEXT,

    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_work_items_status         ON work_items(status);
CREATE INDEX IF NOT EXISTS idx_work_items_scheduled_day  ON work_items(scheduled_day);
CREATE INDEX IF NOT EXISTS idx_work_items_locked         ON work_items(locked);
CREATE UNIQUE INDEX IF NOT EXISTS idx_work_items_slack_ts
    ON work_items(slack_channel, slack_ts) WHERE slack_ts IS NOT NULL;

CREATE TABLE IF NOT EXISTS routines_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_name      TEXT NOT NULL,
    expected_at       TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'Pending',
    completed_at      TEXT,
    accountable_role  TEXT,
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS idx_routines_log_status ON routines_log(status);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _today_iso() -> str:
    return date.today().isoformat()


class WorkManagementStore:
    """Thin, synchronous SQLite wrapper. Safe to instantiate per-call."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------
    # Work items
    # ------------------------------------------------------------------

    def next_id(self) -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM work_items ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return "WI-0001"
        last_n = int(row["id"].split("-")[1])
        return f"WI-{last_n + 1:04d}"

    def create_work_item(self, item: WorkItem) -> WorkItem:
        if not item.id:
            item.id = self.next_id()
        now = _now_iso()
        item.created_at = item.created_at or now
        item.updated_at = now
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO work_items (
                    id, title, description, requested_by, date_requested,
                    urgency, status, approved_by, date_approved, rejection_reason,
                    plan, resources, resources_confirmed, scheduled_day, assigned_to,
                    locked, locked_at, execution_status, execution_started_at,
                    execution_done_at, blocker_note, follow_on_of, notes,
                    slack_channel, slack_ts, created_at, updated_at
                ) VALUES (
                    :id, :title, :description, :requested_by, :date_requested,
                    :urgency, :status, :approved_by, :date_approved, :rejection_reason,
                    :plan, :resources, :resources_confirmed, :scheduled_day, :assigned_to,
                    :locked, :locked_at, :execution_status, :execution_started_at,
                    :execution_done_at, :blocker_note, :follow_on_of, :notes,
                    :slack_channel, :slack_ts, :created_at, :updated_at
                )
                """,
                _to_row(item),
            )
        return item

    def get(self, item_id: str) -> WorkItem | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM work_items WHERE id = ?", (item_id,)
            ).fetchone()
        return _from_row(row) if row else None

    def get_by_slack_ts(self, channel: str, ts: str) -> WorkItem | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM work_items WHERE slack_channel = ? AND slack_ts = ?",
                (channel, ts),
            ).fetchone()
        return _from_row(row) if row else None

    def update(self, item_id: str, **fields) -> WorkItem | None:
        if not fields:
            return self.get(item_id)
        fields = dict(fields)
        for enum_field in ("urgency", "status", "execution_status"):
            if enum_field in fields and hasattr(fields[enum_field], "value"):
                fields[enum_field] = fields[enum_field].value
        for bool_field in ("locked", "resources_confirmed"):
            if bool_field in fields:
                fields[bool_field] = 1 if fields[bool_field] else 0
        for field_name in _NULLABLE_TEXT_FIELDS:
            if fields.get(field_name) == "":
                fields[field_name] = None
        fields["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE work_items SET {set_clause} WHERE id = :id",
                {**fields, "id": item_id},
            )
        return self.get(item_id)

    def list_backlog(self) -> list[WorkItem]:
        """Approved but not yet planned - candidates for the Friday session."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM work_items WHERE status = ? ORDER BY date_requested",
                (WorkItemStatus.APPROVED.value,),
            ).fetchall()
        return [_from_row(r) for r in rows]

    def list_unlocked_upcoming(self, today: str | None = None) -> list[WorkItem]:
        today = today or _today_iso()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM work_items
                WHERE status != ? AND scheduled_day IS NOT NULL
                  AND scheduled_day >= ? AND locked = 0
                """,
                (WorkItemStatus.CANCELLED.value, today),
            ).fetchall()
        return [_from_row(r) for r in rows]

    def list_overdue_approvals(self, sla_hours: int = 24) -> list[WorkItem]:
        cutoff = (datetime.now(UTC) - timedelta(hours=sla_hours)).date().isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM work_items WHERE status = ? AND date_requested <= ?",
                (WorkItemStatus.SUBMITTED.value, cutoff),
            ).fetchall()
        return [_from_row(r) for r in rows]

    def list_scheduled_today(self, today: str | None = None) -> list[WorkItem]:
        today = today or _today_iso()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM work_items WHERE locked = 1 AND scheduled_day = ?",
                (today,),
            ).fetchall()
        return [_from_row(r) for r in rows]

    def list_todays_untouched(self, today: str | None = None) -> list[WorkItem]:
        today = today or _today_iso()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM work_items
                WHERE locked = 1 AND scheduled_day = ? AND execution_status = ?
                """,
                (today, ExecutionStatus.NOT_STARTED.value),
            ).fetchall()
        return [_from_row(r) for r in rows]

    def lock_upcoming_week(self, week_start: str, week_end: str) -> list[WorkItem]:
        """Lock every unlocked item scheduled within [week_start, week_end]."""
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE work_items
                SET locked = 1, locked_at = ?, status = ?, updated_at = ?
                WHERE locked = 0 AND scheduled_day BETWEEN ? AND ?
                """,
                (now, WorkItemStatus.LOCKED.value, now, week_start, week_end),
            )
            rows = conn.execute(
                "SELECT * FROM work_items WHERE locked_at = ?", (now,)
            ).fetchall()
        return [_from_row(r) for r in rows]

    def weekly_rollup(self, week_start: str, week_end: str) -> WeeklyRollup:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN execution_status = 'Done' THEN 1 ELSE 0 END) AS done_count,
                    SUM(CASE WHEN execution_status = 'Blocked' THEN 1 ELSE 0 END) AS blocked_count,
                    SUM(CASE WHEN execution_status = 'In progress' THEN 1 ELSE 0 END) AS in_progress_count,
                    SUM(CASE WHEN execution_status = 'Not started' THEN 1 ELSE 0 END) AS not_started_count,
                    COUNT(*) AS total_locked_items
                FROM work_items
                WHERE locked = 1 AND scheduled_day BETWEEN ? AND ?
                """,
                (week_start, week_end),
            ).fetchone()
        return WeeklyRollup(
            done_count=row["done_count"] or 0,
            blocked_count=row["blocked_count"] or 0,
            in_progress_count=row["in_progress_count"] or 0,
            not_started_count=row["not_started_count"] or 0,
            total_locked_items=row["total_locked_items"] or 0,
            generated_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # Roles
    # ------------------------------------------------------------------

    def get_role(self, role_name: str) -> Role | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM roles WHERE role_name = ?", (role_name,)
            ).fetchone()
        if not row:
            return None
        return Role(**{k: (v or "") for k, v in dict(row).items()})

    def list_roles(self) -> list[Role]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM roles").fetchall()
        return [Role(**{k: (v or "") for k, v in dict(r).items()}) for r in rows]

    def upsert_role(self, role: Role) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO roles (role_name, primary_person, backup_person, slack_user_id, notes)
                VALUES (:role_name, :primary_person, :backup_person, :slack_user_id, :notes)
                ON CONFLICT(role_name) DO UPDATE SET
                    primary_person = excluded.primary_person,
                    backup_person = excluded.backup_person,
                    slack_user_id = excluded.slack_user_id,
                    notes = excluded.notes
                """,
                asdict(role),
            )

    # ------------------------------------------------------------------
    # Routines log
    # ------------------------------------------------------------------

    def log_routine(self, entry: RoutineLogEntry) -> RoutineLogEntry:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO routines_log
                    (routine_name, expected_at, status, completed_at, accountable_role, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.routine_name.value,
                    entry.expected_at,
                    entry.status.value,
                    entry.completed_at or None,
                    entry.accountable_role or None,
                    entry.notes or None,
                ),
            )
            entry.id = cur.lastrowid
        return entry

    def already_logged(self, routine_name: RoutineName, expected_at: str) -> bool:
        """True if this exact routine occurrence has already been logged."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM routines_log WHERE routine_name = ? AND expected_at = ?",
                (routine_name.value, expected_at),
            ).fetchone()
        return row is not None


_NULLABLE_TEXT_FIELDS = (
    "description", "approved_by", "date_approved", "rejection_reason", "plan",
    "resources", "scheduled_day", "assigned_to", "locked_at", "execution_started_at",
    "execution_done_at", "blocker_note", "follow_on_of", "notes",
    "slack_channel", "slack_ts",
)


def _to_row(item: WorkItem) -> dict:
    d = asdict(item)
    d.pop("raw", None)
    d["urgency"] = item.urgency.value if hasattr(item.urgency, "value") else item.urgency
    d["status"] = item.status.value if hasattr(item.status, "value") else item.status
    d["execution_status"] = (
        item.execution_status.value
        if hasattr(item.execution_status, "value")
        else item.execution_status
    )
    d["locked"] = 1 if item.locked else 0
    d["resources_confirmed"] = 1 if item.resources_confirmed else 0
    # Empty strings become NULL - required for follow_on_of's FK check, and
    # keeps "not set" unambiguous for every other optional text column.
    for field_name in _NULLABLE_TEXT_FIELDS:
        if d.get(field_name) == "":
            d[field_name] = None
    return d


def _from_row(row: sqlite3.Row) -> WorkItem:
    d = dict(row)
    d["urgency"] = Urgency(d["urgency"]) if d.get("urgency") else Urgency.ROUTINE
    d["status"] = WorkItemStatus(d["status"]) if d.get("status") else WorkItemStatus.SUBMITTED
    d["execution_status"] = (
        ExecutionStatus(d["execution_status"])
        if d.get("execution_status")
        else ExecutionStatus.NOT_STARTED
    )
    d["locked"] = bool(d.get("locked", 0))
    d["resources_confirmed"] = bool(d.get("resources_confirmed", 0))
    for k in (
        "description", "approved_by", "date_approved", "rejection_reason", "plan",
        "resources", "scheduled_day", "assigned_to", "locked_at", "execution_started_at",
        "execution_done_at", "blocker_note", "follow_on_of", "notes",
        "slack_channel", "slack_ts",
    ):
        if d.get(k) is None:
            d[k] = ""
    return WorkItem(**{k: v for k, v in d.items() if k in WorkItem.__dataclass_fields__})

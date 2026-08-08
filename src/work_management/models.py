"""Work management data model (local SQLite)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Urgency(str, Enum):
    ROUTINE = "Routine"
    URGENT = "Urgent"


class WorkItemStatus(str, Enum):
    SUBMITTED = "Submitted"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    PLANNED = "Planned"
    SCHEDULED = "Scheduled"
    RESOURCED = "Resourced"
    LOCKED = "Locked"
    IN_PROGRESS = "In Progress"
    BLOCKED = "Blocked"
    DONE = "Done"
    CANCELLED = "Cancelled"


class ExecutionStatus(str, Enum):
    NOT_STARTED = "Not started"
    IN_PROGRESS = "In progress"
    BLOCKED = "Blocked"
    DONE = "Done"


class RoutineName(str, Enum):
    WEEKLY_PLANNING = "Weekly planning session"
    APPROVAL_SLA = "Approval SLA check"
    DAILY_EXECUTION = "Daily execution check-in"
    WEEKLY_ACCOUNTABILITY = "Weekly accountability review"


class RoutineStatus(str, Enum):
    PENDING = "Pending"
    DONE = "Done"
    LATE = "Late"
    MISSED = "Missed"
    SKIPPED = "Skipped"


@dataclass
class WorkItem:
    id: str
    title: str
    requested_by: str
    date_requested: str  # ISO date
    description: str = ""
    urgency: Urgency = Urgency.ROUTINE
    status: WorkItemStatus = WorkItemStatus.SUBMITTED

    approved_by: str = ""
    date_approved: str = ""
    rejection_reason: str = ""

    plan: str = ""

    resources: str = ""
    resources_confirmed: bool = False

    scheduled_day: str = ""
    assigned_to: str = ""

    locked: bool = False
    locked_at: str = ""

    execution_status: ExecutionStatus = ExecutionStatus.NOT_STARTED
    execution_started_at: str = ""
    execution_done_at: str = ""
    blocker_note: str = ""

    follow_on_of: str = ""
    notes: str = ""

    # Slack thread anchor - the root message for this item. Reactions are
    # only honoured on this exact message (see reactions.py).
    slack_channel: str = ""
    slack_ts: str = ""

    created_at: str = ""
    updated_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutineLogEntry:
    routine_name: RoutineName
    expected_at: str
    status: RoutineStatus = RoutineStatus.PENDING
    completed_at: str = ""
    accountable_role: str = ""
    notes: str = ""
    id: int | None = None


@dataclass
class Role:
    role_name: str
    primary_person: str
    backup_person: str = ""
    slack_user_id: str = ""
    notes: str = ""


@dataclass
class WeeklyRollup:
    done_count: int = 0
    blocked_count: int = 0
    not_started_count: int = 0
    in_progress_count: int = 0
    total_locked_items: int = 0
    routine_issues: list[str] = field(default_factory=list)
    generated_at: datetime | None = None

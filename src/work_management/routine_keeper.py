"""Routine Keeper - scheduled checks enforcing the weekly Work Management cadence.

Runs entirely outside the Slack message/LangGraph path (see BRS §8 - this is
deliberate). Triggered by systemd timers (deploy/systemd/wm-*.timer), reads
the WorkManagementStore directly, and posts via the same publisher already
used for Frontend Support reports (src/reporting/publisher.py).

Per BRS FR-35, this module must never approve, reject, plan, schedule,
resource, lock, or execute a work item - it only checks and escalates. Each
check is idempotent per day (see `already_logged`) so a systemd restart or a
manual re-run doesn't spam the channel twice in one day.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from src.core.config import settings
from src.reporting.publisher import publish_report_to_channel
from src.work_management.models import (
    RoutineLogEntry,
    RoutineName,
    RoutineStatus,
    WeeklyRollup,
)
from src.work_management.store import WorkManagementStore

logger = logging.getLogger(__name__)


def _store() -> WorkManagementStore:
    return WorkManagementStore(settings.work_management_db_path)


def _post(text: str) -> None:
    publish_report_to_channel(text, channel_id=settings.channel_work_management)


def _mention(store: WorkManagementStore, role_name: str) -> str:
    role = store.get_role(role_name)
    if role and role.slack_user_id:
        return f"<@{role.slack_user_id}>"
    return f"@{role_name}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Check 1: weekly planning session -> locked plan
# AAOM equivalent: Area Schedule Review Meeting, "one meeting held prior to
# the start of each scheduling period" (docs/17_management_routines.md §3.2,
# if you've pulled the AAOM handbook conversion into this repo for reference).
# ---------------------------------------------------------------------------

def check_weekly_planning() -> dict:
    store = _store()
    today = date.today()
    expected_at = today.isoformat()

    if store.already_logged(RoutineName.WEEKLY_PLANNING, expected_at):
        return {"status": "already_checked"}

    unlocked = store.list_unlocked_upcoming(expected_at)
    status = RoutineStatus.DONE if not unlocked else RoutineStatus.MISSED

    text = None
    if unlocked:
        mention = _mention(store, "Work Approver")
        lines = [f"⏰ *Weekly planning check* — {len(unlocked)} scheduled item(s) aren't locked for next week yet."]
        lines += [f"- {i.id}: {i.title} ({i.scheduled_day or 'no day set'})" for i in unlocked[:10]]
        lines.append(f"\n{mention}, ready to `lock the plan`?")
        text = "\n".join(lines)

    store.log_routine(
        RoutineLogEntry(
            routine_name=RoutineName.WEEKLY_PLANNING,
            expected_at=expected_at,
            status=status,
            completed_at=_now(),
            accountable_role="Work Approver",
        )
    )
    if text:
        _post(text)
    logger.info("Weekly planning check: %s (%d unlocked)", status.value, len(unlocked))
    return {"status": status.value, "unlocked_count": len(unlocked)}


# ---------------------------------------------------------------------------
# Check 2: every request decided within SLA
# ---------------------------------------------------------------------------

def check_approval_sla() -> dict:
    store = _store()
    today_iso = date.today().isoformat()

    if store.already_logged(RoutineName.APPROVAL_SLA, today_iso):
        return {"status": "already_checked"}

    overdue = store.list_overdue_approvals(sla_hours=settings.work_approval_sla_hours)
    status = RoutineStatus.DONE if not overdue else RoutineStatus.LATE

    text = None
    if overdue:
        mention = _mention(store, "Work Approver")
        lines = [
            (f"⏰ *Approval SLA check* — {len(overdue)} request(s) waiting "
            f"more than {settings.work_approval_sla_hours}h for a decision:")
        ]
        lines += [f"- {i.id}: {i.title} (requested {i.date_requested})" for i in overdue[:10]]
        lines.append(f"\n{mention}, could you take a look?")
        text = "\n".join(lines)

    store.log_routine(
        RoutineLogEntry(
            routine_name=RoutineName.APPROVAL_SLA,
            expected_at=today_iso,
            status=status,
            completed_at=_now(),
            accountable_role="Work Approver",
        )
    )
    if text:
        _post(text)
    logger.info("Approval SLA check: %s (%d overdue)", status.value, len(overdue))
    return {"status": status.value, "overdue_count": len(overdue)}


# ---------------------------------------------------------------------------
# Check 3: today's scheduled work gets a status update
# ---------------------------------------------------------------------------

def check_daily_execution() -> dict:
    store = _store()
    today_iso = date.today().isoformat()

    if store.already_logged(RoutineName.DAILY_EXECUTION, today_iso):
        return {"status": "already_checked"}

    untouched = store.list_todays_untouched(today_iso)
    status = RoutineStatus.DONE if not untouched else RoutineStatus.LATE

    text = None
    if untouched:
        mention = _mention(store, "Work Executioner")
        lines = [f"⏰ *Daily execution check* — {len(untouched)} item(s) scheduled for today haven't been started:"]
        lines += [f"- {i.id}: {i.title}" for i in untouched[:10]]
        lines.append(f"\n{mention}, react 🚧 when you start one (or reply `start <id>`).")
        text = "\n".join(lines)

    store.log_routine(
        RoutineLogEntry(
            routine_name=RoutineName.DAILY_EXECUTION,
            expected_at=today_iso,
            status=status,
            completed_at=_now(),
            accountable_role="Work Executioner",
        )
    )
    if text:
        _post(text)
    logger.info("Daily execution check: %s (%d untouched)", status.value, len(untouched))
    return {"status": status.value, "untouched_count": len(untouched)}


# ---------------------------------------------------------------------------
# Check 4: weekly accountability rollup
# ---------------------------------------------------------------------------

def run_weekly_accountability_review() -> dict:
    store = _store()
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    expected_at = today.isoformat()

    if store.already_logged(RoutineName.WEEKLY_ACCOUNTABILITY, expected_at):
        return {"status": "already_checked"}

    rollup: WeeklyRollup = store.weekly_rollup(week_start.isoformat(), week_end.isoformat())

    lines = [
        "*Weekly accountability review*",
        f"_{week_start.isoformat()} → {week_end.isoformat()}_",
        f"- Done: {rollup.done_count}",
        f"- In progress: {rollup.in_progress_count}",
        f"- Blocked: {rollup.blocked_count}",
        f"- Not started: {rollup.not_started_count}",
        f"- Total locked items: {rollup.total_locked_items}",
    ]

    store.log_routine(
        RoutineLogEntry(
            routine_name=RoutineName.WEEKLY_ACCOUNTABILITY,
            expected_at=expected_at,
            status=RoutineStatus.DONE,
            completed_at=_now(),
        )
    )
    _post("\n".join(lines))
    logger.info("Weekly accountability review posted: %s", rollup)
    return {"status": "done", "rollup": rollup}

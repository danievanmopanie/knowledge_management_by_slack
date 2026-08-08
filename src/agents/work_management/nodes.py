"""Node functions for the Work Management LangGraph graph.

Each `make_*_node(store, ...)` factory closes over a WorkManagementStore
(and, where useful, an LLM client) and returns the plain `(state) -> dict`
callable LangGraph expects. Keeping them as factories rather than module-level
functions with a global store makes each node independently testable.

Design intent (see docs/WORK_MANAGEMENT.md and the BRS FR-30-FR-35):
- orchestrator: cheap, deterministic intent routing + direct answers for
  read-only intents (help/status/lookup) + creates new work items.
- work_approver: approve/reject/lock decisions. Deterministic - no LLM.
- planner: the one node that benefits from the local LLM - turning a rough
  description into a short what/how/duration plan.
- scheduler, resource_coordinator: deterministic, template responses.
  Chained automatically after planner (mirrors the single back-to-back
  Friday-session conversation flow the skill docs describe).
- work_executioner: deterministic status transitions. Reactions
  (see reactions.py) are the primary path for this; text commands
  ("start WI-0007", "done WI-0007", "blocked WI-0007 <reason>") are a
  fallback that goes through this same node.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from langchain_core.messages import HumanMessage, SystemMessage

from src.work_management.models import (
    ExecutionStatus,
    Urgency,
    WorkItem,
    WorkItemStatus,
)
from src.work_management.store import WorkManagementStore

logger = logging.getLogger(__name__)

WI_PATTERN = re.compile(r"\bWI-(\d{4})\b", re.IGNORECASE)

HELP_TEXT = """*Work Management*

Just describe a problem or request and I'll log it for approval - no special syntax needed.

Once something has an ID (e.g. `WI-0007`):
• `approve WI-0007` / `reject WI-0007 <reason>` - decide on a request (usually done with a ✅/❌ reaction instead)
• `plan WI-0007 [for <day>]` - run planning + scheduling + resourcing on an approved item
• `lock the plan` - lock everything currently scheduled into next week's plan
• `start WI-0007` / `done WI-0007` / `blocked WI-0007 <reason>` - execution status (usually done with 🚧/✔️/⛔ reactions instead)
• `status` - see the backlog and today's work
• `status WI-0007` - see one item's current state
"""

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _extract_wi_id(text: str) -> str | None:
    m = WI_PATTERN.search(text or "")
    return f"WI-{m.group(1)}" if m else None


def _format_item_line(item: WorkItem) -> str:
    bits = [f"*{item.id}*: {item.title}", f"status={item.status.value}"]
    if item.scheduled_day:
        bits.append(f"day={item.scheduled_day}")
    if item.execution_status != ExecutionStatus.NOT_STARTED:
        bits.append(f"exec={item.execution_status.value}")
    return " · ".join(bits)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def make_orchestrator_node(store: WorkManagementStore):
    def node(state: dict) -> dict:
        text = (state.get("message") or "").strip()
        lower = text.lower()
        wi_id = _extract_wi_id(text)

        if not text or lower in {"help", "?"}:
            return {"intent": "help", "response": HELP_TEXT}

        if lower.startswith("status"):
            if wi_id:
                return {"intent": "status_item", "work_item_id": wi_id, "response": _status_item(store, wi_id)}
            return {"intent": "status_all", "response": _status_all(store)}

        if "lock" in lower and "plan" in lower:
            return {"intent": "lock_plan", "response": ""}

        if re.search(r"\bapprove\b", lower) and wi_id:
            return {"intent": "approve", "work_item_id": wi_id, "response": ""}

        if re.search(r"\breject\b", lower) and wi_id:
            return {"intent": "reject", "work_item_id": wi_id, "response": ""}

        if re.search(r"\bplan\b", lower) and wi_id:
            return {"intent": "plan_item", "work_item_id": wi_id, "response": ""}

        if re.search(r"\bstart\b", lower) and wi_id:
            return {"intent": "start_item", "work_item_id": wi_id, "response": ""}

        if re.search(r"\b(done|complete[d]?)\b", lower) and wi_id:
            return {"intent": "done_item", "work_item_id": wi_id, "response": ""}

        if re.search(r"\bblock(ed)?\b", lower) and wi_id:
            return {"intent": "blocked_item", "work_item_id": wi_id, "response": ""}

        if wi_id:
            return {"intent": "lookup", "work_item_id": wi_id, "response": _status_item(store, wi_id)}

        # No known command and no item reference: treat as a new request.
        # This is the deliberately low-friction default - see
        # docs/WORK_MANAGEMENT.md and the Work Approver skill doc.
        item = store.create_work_item(
            WorkItem(
                id="",
                title=text[:120],
                description=text,
                requested_by=state.get("user") or "unknown",
                date_requested=date.today().isoformat(),
                urgency=Urgency.URGENT if "urgent" in lower else Urgency.ROUTINE,
                slack_channel=state.get("channel_id") or "",
                slack_ts=state.get("thread_ts") or "",
            )
        )
        urgency_note = " (flagged *Urgent*)" if item.urgency == Urgency.URGENT else ""
        return {
            "intent": "new_request",
            "work_item_id": item.id,
            "response": (
                f"Logged as *{item.id}*{urgency_note}. Awaiting approval "
                f"(react ✅/❌ on this message, or reply `approve {item.id}` / `reject {item.id} <reason>`)."
            ),
        }

    return node


def _status_all(store: WorkManagementStore) -> str:
    backlog = store.list_backlog()
    today_items = store.list_scheduled_today()
    lines = ["*Backlog (approved, not yet planned)*"]
    lines += [f"- {_format_item_line(i)}" for i in backlog] or ["- (empty)"]
    lines.append("\n*Today's locked work*")
    lines += [f"- {_format_item_line(i)}" for i in today_items] or ["- (nothing scheduled today)"]
    return "\n".join(lines)


def _status_item(store: WorkManagementStore, wi_id: str) -> str:
    item = store.get(wi_id)
    if not item:
        return f"I don't have anything called `{wi_id}`."
    lines = [_format_item_line(item)]
    if item.plan:
        lines.append(f"Plan: {item.plan}")
    if item.resources:
        lines.append(f"Resources: {item.resources} (confirmed={item.resources_confirmed})")
    if item.blocker_note:
        lines.append(f"Blocker: {item.blocker_note}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Work Approver
# ---------------------------------------------------------------------------

def make_work_approver_node(store: WorkManagementStore):
    def node(state: dict) -> dict:
        intent = state["intent"]
        actor = state.get("user") or "someone"

        if intent == "lock_plan":
            today = date.today()
            week_end = today + timedelta(days=6)
            locked = store.lock_upcoming_week(today.isoformat(), week_end.isoformat())
            if not locked:
                return {"response": "Nothing unlocked is currently scheduled - nothing to lock."}
            lines = [f"🔒 Locked {len(locked)} item(s) for the coming week:"]
            lines += [f"- {_format_item_line(i)}" for i in locked]
            return {"response": "\n".join(lines)}

        wi_id = state.get("work_item_id")
        item = store.get(wi_id) if wi_id else None
        if not item:
            return {"response": f"I don't have anything called `{wi_id}`."}
        if item.status != WorkItemStatus.SUBMITTED:
            return {"response": f"*{item.id}* is already `{item.status.value}` - nothing to decide."}

        if intent == "approve":
            store.update(
                item.id,
                status=WorkItemStatus.APPROVED,
                approved_by=actor,
                date_approved=date.today().isoformat(),
            )
            return {"response": f"✅ *{item.id}* approved. It'll be planned in the next Friday session (or sooner if urgent)."}

        if intent == "reject":
            reason = re.sub(r"^.*?\bWI-\d{4}\b", "", state.get("message", ""), flags=re.IGNORECASE).strip(" -:")
            store.update(
                item.id,
                status=WorkItemStatus.REJECTED,
                approved_by=actor,
                date_approved=date.today().isoformat(),
                rejection_reason=reason,
            )
            note = f" Reason: {reason}" if reason else ""
            return {"response": f"❌ *{item.id}* rejected.{note}"}

        return {"response": "Nothing to do."}

    return node


# ---------------------------------------------------------------------------
# Planner (the one node that calls the local LLM)
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """You are helping a small operations team turn an approved work request into a short, \
practical plan during their weekly planning session.

Given the request's title/description and any extra detail supplied just now, write:
1. What needs to be done (1-2 sentences)
2. How it will be done (only if it's not obvious from #1 - skip if redundant)
3. A rough duration estimate (e.g. "~45 min", "half a day")

Keep the whole thing under 60 words. No preamble, no headings - just the plan itself as it \
should appear next to the work item. This is a small team doing routine operational work \
(IT/site support), not a formal engineering project - keep it proportionate.
"""


def make_planner_node(store: WorkManagementStore, llm=None):
    from src.llm.client import get_llm

    llm = llm or get_llm()

    async def node(state: dict) -> dict:
        wi_id = state.get("work_item_id")
        item = store.get(wi_id) if wi_id else None
        if not item:
            return {"response": f"I don't have anything called `{wi_id}`.", "scratch": {"abort": True}}

        extra = re.sub(r"^.*?\bWI-\d{4}\b", "", state.get("message", ""), flags=re.IGNORECASE).strip(" -:")
        user_content = f"Title: {item.title}\nDescription: {item.description or item.title}"
        if extra:
            user_content += f"\nAdditional detail from the team: {extra}"

        try:
            resp = await llm.ainvoke(
                [SystemMessage(content=PLANNER_SYSTEM_PROMPT), HumanMessage(content=user_content)]
            )
            plan_text = (resp.content if hasattr(resp, "content") else str(resp)).strip()
        except Exception as e:
            logger.exception("Planner LLM call failed")
            plan_text = f"(plan generation unavailable: {type(e).__name__}) - captured raw: {user_content}"

        store.update(item.id, plan=plan_text, status=WorkItemStatus.PLANNED)
        return {"response": f"*Plan for {item.id}:* {plan_text}"}

    return node


# ---------------------------------------------------------------------------
# Scheduler (deterministic)
# ---------------------------------------------------------------------------

def _parse_day(text: str) -> str:
    """Very small day-name / 'tomorrow' parser. Defaults to the next day."""
    lower = text.lower()
    today = date.today()
    if "today" in lower:
        return today.isoformat()
    if "tomorrow" in lower:
        return (today + timedelta(days=1)).isoformat()
    for i, name in enumerate(DAY_NAMES):
        if name in lower:
            days_ahead = (i - today.weekday()) % 7
            days_ahead = days_ahead or 7  # if it's today, mean *next* occurrence
            return (today + timedelta(days=days_ahead)).isoformat()
    return (today + timedelta(days=1)).isoformat()


def make_scheduler_node(store: WorkManagementStore):
    def node(state: dict) -> dict:
        if state.get("scratch", {}).get("abort"):
            return {}
        wi_id = state.get("work_item_id")
        item = store.get(wi_id) if wi_id else None
        if not item:
            return {}

        chosen_day = _parse_day(state.get("message", ""))
        same_day = [w for w in store.list_scheduled_today(chosen_day) if w.id != item.id]

        store.update(item.id, scheduled_day=chosen_day, status=WorkItemStatus.SCHEDULED)

        note = ""
        if same_day:
            note = f" ({len(same_day)} other item(s) already on that day - check for clashes)"
        prior = state.get("response", "")
        addition = f"*Scheduled:* {chosen_day}{note}"
        return {"response": f"{prior}\n{addition}" if prior else addition}

    return node


# ---------------------------------------------------------------------------
# Resource Coordinator (deterministic)
# ---------------------------------------------------------------------------

UNAVAILABLE_MARKERS = ("not available", "out of stock", "no stock", "unavailable")


def make_resource_coordinator_node(store: WorkManagementStore):
    """
    Deliberately does NOT try to parse a resources description out of the
    same free-text message the Scheduler just parsed a day out of - an
    earlier version did this and "plan WI-0001 for tuesday" leaked
    "for tuesday" into the resources field. Checking availability is meant
    to be a lightweight confirm/flag, not a description-writer (see
    docs/WORK_MANAGEMENT.md) - if a message names something explicitly
    unavailable, flag it; otherwise confirm against the plan Planner already
    wrote.
    """

    def node(state: dict) -> dict:
        if state.get("scratch", {}).get("abort"):
            return {}
        wi_id = state.get("work_item_id")
        item = store.get(wi_id) if wi_id else None
        if not item:
            return {}

        # Check the original description too, not just the LLM-authored plan -
        # a stated constraint (e.g. "no stock locally") shouldn't be able to
        # silently vanish just because the plan text paraphrased it away.
        combined = f"{item.description} {item.plan} {state.get('message', '')}".lower()
        confirmed = not any(marker in combined for marker in UNAVAILABLE_MARKERS)
        resources_text = (
            "as specified in the plan" if confirmed else "flagged unavailable - needs follow-up before locking"
        )

        store.update(
            item.id,
            resources=resources_text,
            resources_confirmed=confirmed,
            status=WorkItemStatus.RESOURCED,
        )

        flag = "✅ confirmed available" if confirmed else "⚠️ NOT confirmed - flag to Scheduler before locking"
        prior = state.get("response", "")
        addition = f"*Resources:* {resources_text} ({flag})"
        return {"response": f"{prior}\n{addition}" if prior else addition}

    return node


# ---------------------------------------------------------------------------
# Work Executioner
# ---------------------------------------------------------------------------

def make_work_executioner_node(store: WorkManagementStore):
    def node(state: dict) -> dict:
        intent = state["intent"]
        wi_id = state.get("work_item_id")
        item = store.get(wi_id) if wi_id else None
        if not item:
            return {"response": f"I don't have anything called `{wi_id}`."}

        if not item.locked:
            return {"response": f"*{item.id}* isn't part of a locked plan yet - nothing to start/finish."}

        if intent == "start_item":
            store.update(
                item.id,
                execution_status=ExecutionStatus.IN_PROGRESS,
                execution_started_at=datetime.now().isoformat(timespec="seconds"),
            )
            return {"response": f"🚧 *{item.id}* in progress."}

        if intent == "done_item":
            store.update(
                item.id,
                execution_status=ExecutionStatus.DONE,
                execution_done_at=datetime.now().isoformat(timespec="seconds"),
                status=WorkItemStatus.DONE,
            )
            return {"response": f"✔️ *{item.id}* done."}

        if intent == "blocked_item":
            note = re.sub(r"^.*?\bWI-\d{4}\b", "", state.get("message", ""), flags=re.IGNORECASE).strip(" -:")
            store.update(
                item.id,
                execution_status=ExecutionStatus.BLOCKED,
                blocker_note=note or "(no reason given)",
                status=WorkItemStatus.BLOCKED,
            )
            return {"response": f"⛔ *{item.id}* blocked. Reason: {note or '(none given - reply with one)'}"}

        return {"response": "Nothing to do."}

    return node

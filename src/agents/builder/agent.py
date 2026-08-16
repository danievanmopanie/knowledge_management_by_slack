"""Builder Agent – natural Slack interface for the on-device coding harness."""

from __future__ import annotations

import logging
import re

from src.agents.base import BaseAgent
from src.agents.builder.task_store import BuilderTaskStore
from src.core.config import settings
from src.core.context import RequestContext
from src.core.errors import safe_error_message

logger = logging.getLogger(__name__)

HELP = """*Builder Agent*

Talk to me naturally, like a coding assistant. No `build:` prefix is required.

Examples:
• `Have a look at why the frontend tests are failing.`
• `Add a health endpoint and cover it with tests.`
• `What does the builder worker do today?`
• `Take PR #83 and action it on the GX10. Make sure it actually works.`
• `Run the app locally and check the endpoint.`

I work on the GX10 itself. I can inspect the repo, execute real non-interactive
terminal commands as the Builder service account, edit code, run tests and local
runtime checks, and push repaired code back to the same PR when you hand one off.
You do not need to copy/paste generated commands into the GX10 terminal.

Deterministic escape hatches remain available:
• `status <task-id>`
• `cancel <task-id>` — stops a queued turn immediately, or asks a running turn
  to stop at its next safe checkpoint (a Cancel button is also on the status card)
"""

STATUS_RE = re.compile(r"^status\s+(?P<task_id>\S+)$", re.I)
CANCEL_RE = re.compile(r"^cancel\s+(?P<task_id>\S+)$", re.I)
# Backward compatibility only. Natural language is the primary interface.
LEGACY_BUILD_RE = re.compile(r"^build:?\s+(?P<goal>.+)$", re.I | re.S)
_PR_URL_RE = re.compile(r"github\.com/[^\s/]+/[^\s/]+/pull/(?P<number>\d+)", re.I)
_PR_TEXT_RE = re.compile(r"\b(?:pull\s*request|PR)\s*#?\s*(?P<number>\d+)\b", re.I)

HARNESS_INSTRUCTION = """You are the repository-aware Builder coding harness running on the user's GX10.
Treat the latest Slack request as natural language; never require magic command words.
Use the supplied conversation context when it exists.
Use your real on-device terminal tools to inspect, execute and verify instead of asking the user to copy/paste commands.
If the user is asking a question or requesting inspection/explanation only, inspect the repository/device and answer without editing files.
If the user asks for a code/config/test/documentation change, implement it completely in the checked-out worktree.
If an existing PR is handed off, validate and repair that existing PR branch and keep the Slack thread bound to it.
Do not invent repository or runtime facts that you can inspect directly.
"""


def extract_pr_number(text: str) -> str | None:
    """Extract an explicit GitHub PR handoff reference from natural language."""
    if match := _PR_URL_RE.search(text or ""):
        return match.group("number")
    if match := _PR_TEXT_RE.search(text or ""):
        return match.group("number")
    return None


def is_builder_allowed(user_id: str | None) -> bool:
    """Shared allowlist check used by both the chat command and Block Kit buttons.

    Button visibility is never sufficient authorization on its own — a button
    handler must call this again server-side rather than trusting that only an
    allowlisted user could have seen the card.
    """
    allowed = {
        item.strip() for item in settings.builder_agent_allowed_user_ids.split(",") if item.strip()
    }
    return bool(user_id and user_id in allowed)


def cancel_builder_task(store: BuilderTaskStore, task_id: str) -> str:
    """Cancel a queued turn outright, or ask a running turn to stop cooperatively."""
    task = store.get(task_id)
    if task is None:
        return f"No Builder turn found with id `{task_id}`."
    if task["status"] == "pending":
        store.mark_cancelled(task_id)
        return f"Cancelled Builder turn `{task_id}`."
    if task["status"] == "running":
        store.request_cancel(task_id)
        return (
            f"I've asked Builder turn `{task_id}` to stop as soon as it's safely between steps "
            "— it'll skip validation/push and mark itself cancelled. This can take a little "
            "while if a command is already running."
        )
    return f"Builder turn `{task_id}` is already *{task['status']}* and can no longer be cancelled."


class BuilderAgent(BaseAgent):
    """Slack-facing coordinator for natural Builder conversations.

    The Slack event loop never runs the coding harness itself. Every substantive
    conversational turn is queued for the on-device worker so repo inspection,
    terminal execution, code editing and validation remain isolated from Bolt
    event handling.
    """

    name = "builder"

    def __init__(self):
        self.tasks = BuilderTaskStore()

    async def handle(self, message: str, context: RequestContext) -> str:
        text = (message or "").strip()
        if not text or text.lower() in {"help", "?", "hi", "hello"}:
            return HELP

        if not self._is_allowed(context.user_id):
            return (
                "Sorry, you're not on the Builder Agent allowlist for this channel. "
                "Ask an admin to add your Slack user ID to `BUILDER_AGENT_ALLOWED_USER_IDS`."
            )

        try:
            if match := STATUS_RE.match(text):
                return self._status(match.group("task_id"))
            if match := CANCEL_RE.match(text):
                return self._cancel(match.group("task_id"))

            # Preserve old muscle memory without making it the contract.
            if match := LEGACY_BUILD_RE.match(text):
                text = match.group("goal").strip()

            handoff_pr_number = extract_pr_number(text)
            goal = f"{HARNESS_INSTRUCTION}\n\n{text}".strip()
            task_id = self.tasks.enqueue(
                goal=goal,
                requester_id=context.user_id,
                channel_id=context.channel_id,
                thread_ts=context.thread_ts,
                handoff_pr_number=handoff_pr_number,
            )
            queue_note = self._queue_note(task_id)
            if handoff_pr_number:
                return (
                    f"Got it — I’m taking PR #{handoff_pr_number} onto the GX10 as `{task_id}`. "
                    "I’ll check out that PR branch, execute it locally, repair anything I can prove is "
                    f"wrong, run the required gates, and push fixes back to the same PR.{queue_note}"
                )
            return (
                f"Got it — I’ve started working on that as `{task_id}`. "
                "I’ll inspect and execute on the GX10 itself. If code changes are needed, "
                f"I’ll validate them before I publish anything.{queue_note}"
            )
        except Exception:
            logger.exception("Builder agent operation failed request_id=%s", context.request_id)
            return safe_error_message(context.request_id)

    def _queue_note(self, task_id: str) -> str:
        """Queue-position feedback: Builder is a strict single-worker FIFO queue,
        so a turn can legitimately wait behind others for a long time (see
        BUILDER_TURN_DEADLINE_SECONDS). Say so up front instead of leaving the
        user guessing why nothing is happening yet."""
        ahead = self.tasks.count_ahead(task_id)
        if ahead <= 0:
            return ""
        turns = "1 turn" if ahead == 1 else f"{ahead} turns"
        minutes = max(1, settings.builder_turn_deadline_seconds // 60)
        return (
            f" There {'is' if ahead == 1 else 'are'} {turns} ahead of yours on the GX10 "
            f"(each can take up to ~{minutes} min), so I'll get to it once those finish. "
            f"I'll update the `{task_id}` card once I start."
        )

    def _is_allowed(self, user_id: str | None) -> bool:
        return is_builder_allowed(user_id)

    def _status(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if task is None:
            return f"No Builder turn found with id `{task_id}`."
        lines = [
            f"*Builder turn `{task_id}`*",
            f"• Status: *{task['status']}*",
        ]
        if task["status"] == "pending":
            ahead = self.tasks.count_ahead(task_id)
            if ahead > 0:
                lines.append(f"• Queue position: {ahead} turn(s) ahead")
        if task.get("handoff_pr_number"):
            lines.append(f"• Handoff PR: `#{task['handoff_pr_number']}`")
        if task.get("branch_name"):
            lines.append(f"• Branch: `{task['branch_name']}`")
        if task.get("pr_url"):
            lines.append(f"• Pull request: {task['pr_url']}")
        if task.get("error_message"):
            lines.append(f"• Error: {task['error_message']}")
        return "\n".join(lines)

    def _cancel(self, task_id: str) -> str:
        return cancel_builder_task(self.tasks, task_id)

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
• `Change that so a red local build never opens a PR.`

I inspect the repo on the device. If your request only needs an explanation, I
reply without creating a PR. If it changes code, I validate and repair locally
before publishing the PR.

Deterministic escape hatches remain available:
• `status <task-id>`
• `cancel <task-id>` (only while still queued)
"""

STATUS_RE = re.compile(r"^status\s+(?P<task_id>\S+)$", re.I)
CANCEL_RE = re.compile(r"^cancel\s+(?P<task_id>\S+)$", re.I)
# Backward compatibility only. Natural language is the primary interface.
LEGACY_BUILD_RE = re.compile(r"^build:?\s+(?P<goal>.+)$", re.I | re.S)

HARNESS_INSTRUCTION = """You are the repository-aware Builder coding harness running on the user's device.
Treat the latest Slack request as natural language; never require magic command words.
Use the supplied conversation context when it exists.
If the user is asking a question or requesting inspection/explanation only, inspect the repository and answer without editing files.
If the user asks for a code/config/test/documentation change, implement it completely in the worktree.
Do not invent repository facts that you can inspect directly.
"""


class BuilderAgent(BaseAgent):
    """Slack-facing coordinator for natural Builder conversations.

    The Slack event loop never runs the coding harness itself. Every substantive
    conversational turn is queued for the on-device worker so repo inspection,
    code editing and validation remain isolated from Bolt event handling.
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

            goal = f"{HARNESS_INSTRUCTION}\n\n{text}".strip()
            task_id = self.tasks.enqueue(
                goal=goal,
                requester_id=context.user_id,
                channel_id=context.channel_id,
                thread_ts=context.thread_ts,
            )
            return (
                f"Got it — I’ve started working on that as `{task_id}`. "
                "I’ll inspect the repo on the device and reply in this thread. "
                "If code changes are needed, I’ll validate them before I publish anything."
            )
        except Exception:
            logger.exception("Builder agent operation failed request_id=%s", context.request_id)
            return safe_error_message(context.request_id)

    def _is_allowed(self, user_id: str | None) -> bool:
        allowed = {
            item.strip()
            for item in settings.builder_agent_allowed_user_ids.split(",")
            if item.strip()
        }
        return bool(user_id and user_id in allowed)

    def _status(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if task is None:
            return f"No Builder turn found with id `{task_id}`."
        lines = [
            f"*Builder turn `{task_id}`*",
            f"• Status: *{task['status']}*",
        ]
        if task.get("branch_name"):
            lines.append(f"• Branch: `{task['branch_name']}`")
        if task.get("pr_url"):
            lines.append(f"• Pull request: {task['pr_url']}")
        if task.get("error_message"):
            lines.append(f"• Error: {task['error_message']}")
        return "\n".join(lines)

    def _cancel(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if task is None:
            return f"No Builder turn found with id `{task_id}`."
        if task["status"] != "pending":
            return (
                f"Builder turn `{task_id}` is already *{task['status']}* "
                "and can no longer be cancelled."
            )
        self.tasks.mark_cancelled(task_id)
        return f"Cancelled Builder turn `{task_id}`."

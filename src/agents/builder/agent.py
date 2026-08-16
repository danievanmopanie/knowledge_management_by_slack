"""Builder Agent – queues Aider-driven coding tasks from Slack for a background worker."""

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

Post a coding task and the device worker will implement it, run the local test
suite, repair failures, and only then push a branch and open a pull request.

• `build: <describe the change you want>`
• `status <task-id>`
• `cancel <task-id>` (only while still queued)
"""

BUILD_RE = re.compile(r"^build:?\s+(?P<goal>.+)$", re.I | re.S)
STATUS_RE = re.compile(r"^status\s+(?P<task_id>\S+)$", re.I)
CANCEL_RE = re.compile(r"^cancel\s+(?P<task_id>\S+)$", re.I)


class BuilderAgent(BaseAgent):
    """Slack-facing coordinator for the Builder Agent task queue.

    Only ever enqueues tasks into BuilderTaskStore and returns immediately —
    never runs Aider itself, so the Bolt event loop is never blocked. A
    separate long-running worker process (src/worker/builder_worker.py)
    executes queued tasks.
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
            if match := BUILD_RE.match(text):
                goal = match.group("goal").strip()
                if not goal:
                    return "Please describe the change after `build:`."
                task_id = self.tasks.enqueue(
                    goal=goal,
                    requester_id=context.user_id,
                    channel_id=context.channel_id,
                    thread_ts=context.thread_ts,
                )
                return (
                    f"Queued build task `{task_id}`.\n"
                    "The device worker will implement it, validate it locally, and repair "
                    "test failures before it is allowed to publish a PR. "
                    f"Check progress with `status {task_id}`."
                )
            if match := STATUS_RE.match(text):
                return self._status(match.group("task_id"))
            if match := CANCEL_RE.match(text):
                return self._cancel(match.group("task_id"))
            return HELP
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
            return f"No build task found with id `{task_id}`."
        lines = [
            f"*Build task `{task_id}`*",
            f"• Status: *{task['status']}*",
            f"• Goal: {task['goal']}",
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
            return f"No build task found with id `{task_id}`."
        if task["status"] != "pending":
            return (
                f"Build task `{task_id}` is already *{task['status']}* "
                "and can no longer be cancelled."
            )
        self.tasks.mark_cancelled(task_id)
        return f"Cancelled build task `{task_id}`."

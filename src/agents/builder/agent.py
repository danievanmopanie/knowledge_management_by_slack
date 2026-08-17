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
• `Check my PRs, please.`
• `Have a look at why the frontend tests are failing.`
• `Add a health endpoint and cover it with tests.`
• `What does the builder worker do today?`
• `Take PR #83 and action it on the GX10. Make sure it actually works.`
• `Run the app locally and check the endpoint.`

I work on the GX10 itself. I can inspect the repo, execute real non-interactive
terminal commands as the Builder service account, edit code, run tests and local
runtime checks, and push repaired code back to the same PR when you hand one off.
You do not need to copy/paste generated commands into the GX10 terminal.

Long-running work gets one live progress card in the Slack thread. It is updated
in place with phase changes, elapsed time and periodic heartbeats so silence never
looks like a frozen process.

Deterministic escape hatches remain available:
• `status <task-id>`
• `cancel <task-id>` (only while still queued)
"""

STATUS_RE = re.compile(r"^status\s+(?P<task_id>\S+)$", re.I)
CANCEL_RE = re.compile(r"^cancel\s+(?P<task_id>\S+)$", re.I)
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
When list_pull_requests returns list_result_count, report that exact number. Never infer, estimate or recount the total yourself.
Do not invent repository or runtime facts that you can inspect directly.
"""


def extract_pr_number(text: str) -> str | None:
    if match := _PR_URL_RE.search(text or ""):
        return match.group("number")
    if match := _PR_TEXT_RE.search(text or ""):
        return match.group("number")
    return None


class BuilderAgent(BaseAgent):
    """Slack-facing coordinator for natural Builder conversations."""

    name = "builder"

    def __init__(self):
        self.tasks = BuilderTaskStore()

    async def handle(self, message: str, context: RequestContext) -> str:
        text = (message or "").strip()
        if not text or text.lower() in {"help", "?"}:
            return HELP
        if text.lower() in {"hi", "hello", "hey"}:
            return "Hi — tell me what you want me to inspect, explain, fix or run on the GX10."

        if not self._is_allowed(context.user_id):
            return (
                "Sorry, you're not on the Builder Agent allowlist for this channel. "
                "Human users belong in `BUILDER_AGENT_ALLOWED_USER_IDS`; trusted external "
                "app/bot identities belong in `BUILDER_TRUSTED_SLACK_SENDER_IDS`."
            )

        try:
            if match := STATUS_RE.match(text):
                return self._status(match.group("task_id"))
            if match := CANCEL_RE.match(text):
                return self._cancel(match.group("task_id"))

            if match := LEGACY_BUILD_RE.match(text):
                text = match.group("goal").strip()

            handoff_pr_number = extract_pr_number(text)
            goal = f"{HARNESS_INSTRUCTION}\n\n{text}".strip()
            self.tasks.enqueue(
                goal=goal,
                requester_id=context.user_id,
                channel_id=context.channel_id,
                thread_ts=context.thread_ts,
                handoff_pr_number=handoff_pr_number,
            )
            if handoff_pr_number:
                return (
                    f"On it — I’m taking PR #{handoff_pr_number} onto the GX10 now. "
                    "I’ll inspect the actual PR branch, run the required checks, repair only what I can prove is wrong, "
                    "and keep this thread updated while I work."
                )
            return (
                "On it — I’m working on that on the GX10 now. "
                "I’ll keep this thread updated while I inspect, execute and validate the result."
            )
        except Exception:
            logger.exception("Builder agent operation failed request_id=%s", context.request_id)
            return safe_error_message(context.request_id)

    def _is_allowed(self, user_id: str | None) -> bool:
        human = {
            item.strip()
            for item in settings.builder_agent_allowed_user_ids.split(",")
            if item.strip()
        }
        trusted = {
            item.strip()
            for item in settings.builder_trusted_slack_sender_ids.split(",")
            if item.strip()
        }
        return bool(user_id and user_id in (human | trusted))

    def _status(self, task_id: str) -> str:
        task = self.tasks.get(task_id)
        if task is None:
            return f"No Builder turn found with id `{task_id}`."
        lines = [f"*Builder turn `{task_id}`*", f"• Status: *{task['status']}*"]
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
        task = self.tasks.get(task_id)
        if task is None:
            return f"No Builder turn found with id `{task_id}`."
        if task["status"] != "pending":
            return f"Builder turn `{task_id}` is already *{task['status']}* and can no longer be cancelled."
        self.tasks.mark_cancelled(task_id)
        return f"Cancelled Builder turn `{task_id}`."

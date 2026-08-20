"""Work Management agent backed by Taskwondo (external system of record).

Techs create and update work items from Slack; the agent acts with a
service-account API key but resolves the requester's Slack identity to their
Taskwondo user so every item is assigned to the real human.
"""

from __future__ import annotations

import logging
import re

from src.agents.base import BaseAgent
from src.core.config import settings
from src.core.context import RequestContext
from src.core.errors import safe_error_message
from src.identity.commands import try_link_me
from src.identity.resolver import IdentityResolutionError, IdentityResolver
from src.integrations import taskwondo_client

logger = logging.getLogger(__name__)

HELP = """*Work Management* (Taskwondo)

• `create task <title>` — new task assigned to you
• `create bug <title>` — new bug assigned to you
• `create task <title> in <PROJECT>` — choose a project key
• `my tasks` — work items assigned to you
• `task <ID> status <status>` — update a work item (e.g. `task OPS-7 status in_progress`)
• `link me <email>` — link your Slack account to Taskwondo (one time)
"""

_CREATE_RE = re.compile(
    r"^(?:create|new|log|open)\s+(?P<type>task|bug|ticket|feedback|epic)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
_IN_PROJECT_RE = re.compile(r"\s+in\s+(?P<project>[A-Za-z0-9_-]+)\s*$")
_MY_RE = re.compile(r"^my\s+(?:open\s+)?(?:tasks|work|items)$", re.IGNORECASE)
_STATUS_RE = re.compile(
    r"^(?:task|set\s+task|update\s+task)\s+(?P<id>\S+)\s+(?:status\s+)?(?P<status>[A-Za-z0-9_\- ]+?)$",
    re.IGNORECASE,
)


class WorkManagementAgent(BaseAgent):
    """Slack coordinator that reads and writes work items in Taskwondo."""

    name = "work_management"

    def __init__(self, client=taskwondo_client, resolver: IdentityResolver | None = None):
        self.client = client
        self.resolver = resolver or IdentityResolver()

    async def handle(self, message: str, context: RequestContext) -> str:
        text = (message or "").strip()
        if not text or text.lower() in {"help", "?", "hi", "hello"}:
            return HELP

        linked = try_link_me(text, context, self.resolver)
        if linked is not None:
            return linked

        try:
            if match := _CREATE_RE.match(text):
                return self._create(match, context)
            if _MY_RE.match(text):
                return self._my_work(context)
            if match := _STATUS_RE.match(text):
                return self._update_status(match.group("id"), match.group("status").strip(), context)
        except IdentityResolutionError as exc:
            return str(exc)
        except taskwondo_client.TaskwondoClientError as exc:
            logger.warning("Taskwondo error request_id=%s: %s", context.request_id, exc)
            return f"Taskwondo request failed: {exc}"
        except Exception:
            logger.exception("Work management failed request_id=%s", context.request_id)
            return safe_error_message(context.request_id)

        return HELP

    def _create(self, match: re.Match, context: RequestContext) -> str:
        item_type = match.group("type").lower()
        rest = match.group("rest").strip()
        project = None
        if project_match := _IN_PROJECT_RE.search(rest):
            project = project_match.group("project")
            rest = rest[: project_match.start()].strip()
        if not rest:
            return "Please include a title, e.g. `create task Replace docking station`."

        identity = self.resolver.resolve(context, want_taskwondo=True)
        item = self.client.create_work_item(
            title=rest,
            item_type=item_type,
            assignee_id=identity.taskwondo_user_id,
            project=project,
            description=identity.stamp(),
        )
        display = item.get("display_id") or item.get("id") or "(new)"
        link = self._deep_link(display)
        suffix = f"\n{link}" if link else ""
        return f"Created {item_type} *{display}* — assigned to you.{suffix}"

    def _my_work(self, context: RequestContext) -> str:
        identity = self.resolver.resolve(context, want_taskwondo=True)
        rows = self.client.list_work_items(assignee_id=identity.taskwondo_user_id, limit=20)
        if not rows:
            return "You have no open work items in Taskwondo."
        lines = ["*Your Taskwondo work items*"]
        for row in rows[:20]:
            display = row.get("display_id") or row.get("id") or "?"
            title = row.get("title") or ""
            status = row.get("status") or row.get("status_name") or ""
            state = f" [{status}]" if status else ""
            lines.append(f"• `{display}`{state} — {title}")
        return "\n".join(lines)

    def _update_status(self, work_item_id: str, status: str, context: RequestContext) -> str:
        # Resolve identity so only linked users can mutate, keeping the audit trail human.
        self.resolver.resolve(context, want_taskwondo=True)
        normalized = status.strip().replace(" ", "_").lower()
        self.client.update_work_item(work_item_id, status=normalized)
        link = self._deep_link(work_item_id)
        suffix = f"\n{link}" if link else ""
        return f"Updated *{work_item_id}* → status `{normalized}`.{suffix}"

    @staticmethod
    def _deep_link(work_item_id: str) -> str:
        base = (settings.taskwondo_deep_link_base or settings.taskwondo_base_url or "").strip().rstrip("/")
        if not base or not work_item_id or work_item_id == "(new)":
            return ""
        return f"{base}/work-items/{work_item_id}"

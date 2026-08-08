"""Map Slack emoji reactions to work-item actions.

This is the primary interaction path for the two named personas (Senior IM
Site Manager, IT Tech) - see docs/WORK_MANAGEMENT.md. Reactions are only
honoured on a work item's root message (the one posted when it was created
or when its plan/schedule/resource summary was posted) - see
WorkItem.slack_ts. Reacting on any other message in the thread is a no-op.

Deliberately reuses the same node functions as the text-command path
(nodes.py) rather than re-implementing approve/reject/start/done/blocked
logic a second time - one place owns the business rules, two ways to
trigger them.
"""

from __future__ import annotations

import logging

from src.agents.work_management.nodes import (
    make_work_approver_node,
    make_work_executioner_node,
)
from src.work_management.store import WorkManagementStore

logger = logging.getLogger(__name__)

# Slack reaction "names" (no colons) -> (intent, which node handles it)
_APPROVAL_REACTIONS = {
    "white_check_mark": "approve",  # ✅
    "x": "reject",  # ❌
}

_EXECUTION_REACTIONS = {
    "construction": "start_item",  # 🚧
    "heavy_check_mark": "done_item",  # ✔️
    "no_entry": "blocked_item",  # ⛔
}

# Only the Work Approver (by configured Slack user ID) can approve/reject/
# lock. If the role isn't configured yet, we don't block on it - see
# docs/WORK_MANAGEMENT.md "Permissions (v1)".
_APPROVER_ROLE = "Work Approver"


class ReactionRouter:
    def __init__(self, store: WorkManagementStore):
        self.store = store
        self._work_approver = make_work_approver_node(store)
        self._work_executioner = make_work_executioner_node(store)

    def handle(self, event: dict) -> str | None:
        """Return a response string to post in-thread, or None to stay silent
        (unknown reaction, reaction on an untracked message, etc.)."""
        reaction = event.get("reaction", "")
        item_ref = event.get("item", {})
        channel = item_ref.get("channel")
        ts = item_ref.get("ts")
        actor = event.get("user")

        if not channel or not ts:
            return None

        work_item = self.store.get_by_slack_ts(channel, ts)
        if not work_item:
            return None  # reaction on some other message - not ours

        if reaction in _APPROVAL_REACTIONS:
            return self._handle_approval(reaction, work_item.id, actor)

        if reaction in _EXECUTION_REACTIONS:
            intent = _EXECUTION_REACTIONS[reaction]
            state = {"intent": intent, "work_item_id": work_item.id, "message": "", "user": actor}
            return self._work_executioner(state).get("response")

        return None

    def _handle_approval(self, reaction: str, work_item_id: str, actor: str | None) -> str:
        role = self.store.get_role(_APPROVER_ROLE)
        if role and role.slack_user_id and actor and actor != role.slack_user_id:
            return (
                f"Noted, but only <@{role.slack_user_id}> (or their backup) can approve/reject "
                f"here - no change made to *{work_item_id}*."
            )
        intent = _APPROVAL_REACTIONS[reaction]
        state = {"intent": intent, "work_item_id": work_item_id, "message": "", "user": actor}
        return self._work_approver(state).get("response")

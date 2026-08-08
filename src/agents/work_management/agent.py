"""Work Management Orchestrator - entry point for #work-management.

Delegates to a LangGraph graph (see graph.py) with six nodes matching BRS
FR-30: this orchestrator node plus Work Approver, Planner, Scheduler,
Resource Coordinator and Work Executioner. Text messages go through
`handle()` -> the graph; reactions (the primary path for the two named
personas - see docs/WORK_MANAGEMENT.md) go through `handle_reaction()` ->
ReactionRouter, which reuses the same node logic rather than duplicating it.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents.base import BaseAgent
from src.agents.work_management.graph import build_graph
from src.agents.work_management.reactions import ReactionRouter
from src.core.config import settings
from src.work_management.store import WorkManagementStore

logger = logging.getLogger(__name__)


class WorkManagementAgent(BaseAgent):
    """Orchestrates Work Approver, Planner, Scheduler, Resource Coordinator
    and Work Executioner specialists via a LangGraph graph."""

    name = "work_management"

    def __init__(self, store: WorkManagementStore | None = None):
        self.store = store or WorkManagementStore(settings.work_management_db_path)
        self.graph = build_graph(self.store)
        self.reactions = ReactionRouter(self.store)

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        state: dict[str, Any] = {
            "message": message,
            "user": context.get("user"),
            "channel_id": context.get("channel_id"),
            "thread_ts": context.get("thread_ts"),
            "scratch": {},
        }
        try:
            result = await self.graph.ainvoke(state)
        except Exception as e:
            logger.exception("Work management graph failed")
            return f"Sorry, something went wrong handling that.\n`{type(e).__name__}: {e}`"
        return result.get("response") or "Done."

    async def handle_reaction(self, event: dict[str, Any], context: dict[str, Any]) -> str | None:
        try:
            return self.reactions.handle(event)
        except Exception as e:
            logger.exception("Work management reaction handling failed")
            return f"Sorry, something went wrong handling that reaction.\n`{type(e).__name__}: {e}`"

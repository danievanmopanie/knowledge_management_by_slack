"""Work Management multi-agent orchestrator (Planner + Scheduler + Resource Coordinator)."""

from src.agents.base import BaseAgent
from src.core.context import RequestContext


class WorkManagementAgent(BaseAgent):
    """Orchestrates Planner, Scheduler and Resource Coordinator specialists."""

    name = "work_management"

    async def handle(self, message: str, context: RequestContext) -> str:
        # TODO: Implement multi-agent orchestration with LangGraph
        return (
            "Work Management Orchestrator received your request.\n"
            "Planner / Scheduler / Resource Coordinator agents will be wired next.\n\n"
            f"You asked: {message}"
        )

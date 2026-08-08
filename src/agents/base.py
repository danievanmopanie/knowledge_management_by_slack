"""Base agent interface and shared utilities."""

from abc import ABC, abstractmethod
from typing import Any


class BaseAgent(ABC):
    """Abstract base class for all channel agents."""

    name: str = "base"

    @abstractmethod
    async def handle(self, message: str, context: dict[str, Any]) -> str:
        """Process a user message and return a response."""
        ...

    async def handle_reaction(self, event: dict[str, Any], context: dict[str, Any]) -> str | None:
        """
        Process a Slack `reaction_added` event and optionally return a
        response to post in-thread.

        Default is a no-op (None) so existing agents that don't need
        reaction handling aren't forced to implement it. Override in
        agents that do (currently only WorkManagementAgent).
        """
        return None

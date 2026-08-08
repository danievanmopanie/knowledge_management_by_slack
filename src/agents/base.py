"""Base agent interface and shared utilities."""

from abc import ABC, abstractmethod

from src.core.context import RequestContext


class BaseAgent(ABC):
    """Abstract base class for all channel agents."""

    name: str = "base"

    @abstractmethod
    async def handle(self, message: str, context: RequestContext) -> str:
        """Process a user message and return a response."""
        ...

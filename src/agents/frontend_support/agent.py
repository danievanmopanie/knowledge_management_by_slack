"""Frontend Support Knowledge Agent."""

from typing import Any

from src.agents.base import BaseAgent


class FrontendSupportAgent(BaseAgent):
    """Answers support questions using organisational knowledge (RAG)."""

    name = "frontend_support"

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        # TODO: Implement RAG retrieval + LLM generation
        return (
            "Frontend Support Agent received your question.\n"
            "RAG + local LLM integration coming next.\n\n"
            f"You asked: {message}"
        )

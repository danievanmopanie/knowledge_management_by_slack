"""Inventory Management Agent."""

from typing import Any

from src.agents.base import BaseAgent


class InventoryAgent(BaseAgent):
    """Handles inventory queries and basic inventory operations."""

    name = "inventory"

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        # TODO: Implement inventory tools + LLM
        return (
            "Inventory Agent received your request.\n"
            f"You asked: {message}"
        )

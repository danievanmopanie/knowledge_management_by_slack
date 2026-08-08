"""Composed inventory command service.

The large deterministic command parser lives in `base_commands`; focused command
families can extend it without growing one monolithic file indefinitely.
"""

from __future__ import annotations

from src.inventory.base_commands import InventoryCommandService as BaseInventoryCommandService
from src.inventory.profile_commands import AssetProfileCommandService
from src.inventory.repository import InventoryRepository


class InventoryCommandService(BaseInventoryCommandService):
    """Inventory commands plus serialized-asset lifecycle profile operations."""

    def __init__(self, repository: InventoryRepository | None = None):
        super().__init__(repository)
        self.profile_commands = AssetProfileCommandService(self.repository)

    def execute(self, message: str, *, actor: str) -> str:
        text = " ".join((message or "").strip().split())
        result = self.profile_commands.execute_if_match(text, actor=actor)
        if result is not None:
            return result
        return super().execute(text, actor=actor)

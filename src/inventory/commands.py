"""Composed inventory command service.

The large deterministic command parser lives in `base_commands`; focused command
families can extend it without growing one monolithic file indefinitely.
"""

from __future__ import annotations

from src.inventory.asset_ops_commands import AssetOpsCommandService
from src.inventory.audit_commands import AssetAuditCommandService
from src.inventory.audit_correction_commands import AssetAuditCorrectionCommandService
from src.inventory.audit_finding_commands import AssetAuditFindingCommandService
from src.inventory.base_commands import InventoryCommandService as BaseInventoryCommandService
from src.inventory.domain import InventoryDomainError
from src.inventory.loss_commands import AssetLossCommandService
from src.inventory.lost_investigation_commands import LostAssetInvestigationCommandService
from src.inventory.profile_commands import AssetProfileCommandService
from src.inventory.repository import InventoryRepository


class InventoryCommandService(BaseInventoryCommandService):
    """Inventory commands plus focused lifecycle and physical-audit command families."""

    def __init__(self, repository: InventoryRepository | None = None):
        super().__init__(repository)
        self.asset_ops_commands = AssetOpsCommandService(self.repository)
        self.profile_commands = AssetProfileCommandService(self.repository)
        self.audit_commands = AssetAuditCommandService(self.repository)
        self.audit_finding_commands = AssetAuditFindingCommandService(self.repository)
        self.audit_correction_commands = AssetAuditCorrectionCommandService(self.repository)
        self.lost_investigation_commands = LostAssetInvestigationCommandService(self.repository)
        self.loss_commands = AssetLossCommandService(self.repository)

    def execute(self, message: str, *, actor: str) -> str:
        raw = (message or "").strip()
        if len([line for line in raw.splitlines() if line.strip()]) > 1:
            raise InventoryDomainError(
                "Send one inventory command per Slack message so fields and notes cannot be merged accidentally."
            )
        text = " ".join(raw.split())
        for service in (
            self.asset_ops_commands,
            self.profile_commands,
            self.audit_commands,
            self.audit_finding_commands,
            self.audit_correction_commands,
            self.lost_investigation_commands,
            self.loss_commands,
        ):
            result = service.execute_if_match(text, actor=actor)
            if result is not None:
                return result
        return super().execute(text, actor=actor)

"""Slack commands for confirmed-loss approval, write-off and recovery."""

from __future__ import annotations

import re

from src.inventory.loss_governance import AssetLossGovernanceService
from src.inventory.repository import InventoryRepository


class AssetLossCommandService:
    def __init__(self, repository: InventoryRepository):
        self.losses = AssetLossGovernanceService(repository)

    def execute_if_match(self, text: str, *, actor: str) -> str | None:
        handlers = (self._request, self._approve, self._writeoff, self._recover, self._list, self._history, self._detail)
        for handler in handlers:
            result = handler(text, actor)
            if result is not None:
                return result
        return None

    def _request(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"request loss (\S+) note (.+)", text, re.IGNORECASE)
        if not match:
            return None
        item = self.losses.request(match.group(1), actor=actor, note=match.group(2))
        return f"Created loss case `{item.loss_id}` for asset `{item.asset_id}`. Status: *{item.status}*."

    def _approve(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"approve loss (\S+) financial (\S+) note (.+)", text, re.IGNORECASE)
        if not match:
            return None
        loss_id, reference, note = match.groups()
        item = self.losses.approve(loss_id, actor=actor, financial_reference=reference, note=note)
        return f"Approved loss case `{item.loss_id}` with financial reference `{item.financial_reference}`."

    def _writeoff(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"write off loss (\S+) reference (\S+) note (.+)", text, re.IGNORECASE)
        if not match:
            return None
        loss_id, reference, note = match.groups()
        item = self.losses.write_off(loss_id, actor=actor, writeoff_reference=reference, note=note)
        return f"Financially wrote off asset `{item.asset_id}` under `{item.writeoff_reference}`."

    def _recover(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"recover loss (\S+) at (\S+) reference (\S+) note (.+)", text, re.IGNORECASE)
        if not match:
            return None
        loss_id, location, reference, note = match.groups()
        item = self.losses.recover(loss_id, actor=actor, location_id=location, recovery_reference=reference, note=note)
        return f"Recovered asset `{item.asset_id}` to `{item.recovery_location}` under `{item.recovery_reference}`."

    def _list(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"loss cases(?: (pending_approval|approved|written_off|recovered|all))?(?: asset (\S+))?", text, re.IGNORECASE)
        if not match:
            return None
        status, asset_id = match.groups()
        rows = self.losses.list(status=(status or "all").lower(), asset_id=asset_id or "")
        if not rows:
            return "No matching asset loss cases."
        lines = ["*Asset loss cases*"]
        for item in rows[:50]:
            lines.append(f"• `{item.loss_id}` — asset `{item.asset_id}` — *{item.status}* — investigation `{item.investigation_id}`")
        return "\n".join(lines)

    def _history(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"loss history (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        events = self.losses.events(match.group(1))
        return "\n".join([f"*Loss history — {match.group(1)}*"] + [f"• `{e.at.isoformat()}` — *{e.action}* by `{e.actor}` — {e.note}" for e in events])

    def _detail(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"loss case (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        item = self.losses.require(match.group(1))
        return (
            f"*Loss case {item.loss_id}*\n"
            f"• Asset: `{item.asset_id}`\n• Investigation: `{item.investigation_id}`\n"
            f"• Status: *{item.status}*\n• Requested by: `{item.requested_by}`\n"
            f"• Approved by: `{item.approved_by or '—'}`\n• Financial ref: `{item.financial_reference or '—'}`\n"
            f"• Write-off ref: `{item.writeoff_reference or '—'}`\n"
            f"• Recovery: `{item.recovery_reference or '—'}` @ `{item.recovery_location or '—'}`"
        )

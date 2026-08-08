"""Slack-facing commands for explicit serialized-asset audit corrections."""

from __future__ import annotations

import re

from src.inventory.audit_corrections import AssetAuditCorrectionService
from src.inventory.repository import InventoryRepository


class AssetAuditCorrectionCommandService:
    def __init__(self, repository: InventoryRepository):
        self.corrections = AssetAuditCorrectionService(repository)

    def execute_if_match(self, text: str, *, actor: str) -> str | None:
        handlers = (
            self._relocate,
            self._lost_investigation,
            self._dismiss_unknown,
            self._list_asset,
            self._detail,
        )
        for handler in handlers:
            result = handler(text, actor)
            if result is not None:
                return result
        return None

    def _relocate(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"correct audit finding (\S+) relocate(?: to (\S+))? note (.+)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        finding_id, destination, note = match.groups()
        correction = self.corrections.relocate(
            finding_id,
            actor=actor,
            to_location=destination or "",
            note=note,
        )
        return (
            f"Applied audit correction `{correction.correction_id}` for finding `{finding_id}`. "
            f"Asset `{correction.asset_id}` moved from `{correction.from_location}` to "
            f"`{correction.to_location}`."
        )

    def _lost_investigation(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"correct audit finding (\S+) lost investigation note (.+)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        finding_id, note = match.groups()
        correction = self.corrections.start_lost_investigation(
            finding_id,
            actor=actor,
            note=note,
        )
        return (
            f"Started lost-asset investigation via correction `{correction.correction_id}` "
            f"for asset `{correction.asset_id}` and finding `{finding_id}`."
        )

    def _dismiss_unknown(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"correct audit finding (\S+) dismiss unknown note (.+)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        finding_id, note = match.groups()
        correction = self.corrections.dismiss_unknown_scan(
            finding_id,
            actor=actor,
            note=note,
        )
        return (
            f"Dismissed unknown scan with correction `{correction.correction_id}` "
            f"for finding `{finding_id}`."
        )

    def _list_asset(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"asset corrections (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        asset_id = match.group(1)
        rows = self.corrections.list(asset_id=asset_id)
        if not rows:
            return f"Asset `{asset_id}` has no audit corrections."
        lines = [f"*Audit corrections — {asset_id}*"]
        for item in rows:
            lines.append(
                f"• `{item.correction_id}` *{item.action}* — finding `{item.finding_id}` — "
                f"`{item.from_location or '—'}` → `{item.to_location or '—'}` — {item.note}"
            )
        return "\n".join(lines)

    def _detail(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"audit correction (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        item = self.corrections.require(match.group(1))
        return (
            f"*Audit correction {item.correction_id}*\n"
            f"• Finding: `{item.finding_id}`\n"
            f"• Action: *{item.action}*\n"
            f"• Asset: `{item.asset_id or '—'}`\n"
            f"• Location: `{item.from_location or '—'}` → `{item.to_location or '—'}`\n"
            f"• Actor: `{item.actor}`\n"
            f"• At: `{item.at.isoformat()}`\n"
            f"• Note: {item.note}"
        )

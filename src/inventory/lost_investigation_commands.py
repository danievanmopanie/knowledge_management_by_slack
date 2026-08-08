"""Slack commands for lost-asset investigations."""

from __future__ import annotations

import re

from src.inventory.domain import InventoryDomainError
from src.inventory.lost_investigations import LostAssetInvestigationService
from src.inventory.repository import InventoryRepository


class LostAssetInvestigationCommandService:
    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self.investigations = LostAssetInvestigationService(repository)

    def execute_if_match(self, text: str, *, actor: str) -> str | None:
        handlers = (
            self._open,
            self._list,
            self._assign,
            self._note,
            self._close,
            self._history,
            self._detail,
        )
        for handler in handlers:
            result = handler(text, actor)
            if result is not None:
                return result
        return None

    def _open(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"open lost investigation (\S+)(?: owner (\S+))?", text, re.IGNORECASE)
        if not match:
            return None
        correction_id, owner = match.groups()
        with self.repository.transaction() as conn:
            correction = conn.execute(
                "SELECT * FROM inventory_asset_audit_corrections WHERE correction_id=?",
                (correction_id,),
            ).fetchone()
            if not correction:
                raise InventoryDomainError(f"Unknown asset audit correction: {correction_id}")
            if correction["action"] != "lost_investigation":
                raise InventoryDomainError(
                    "Lost-asset investigations can only be opened from a lost_investigation correction."
                )
            existing = conn.execute(
                "SELECT investigation_id FROM inventory_lost_asset_investigations WHERE correction_id=?",
                (correction_id,),
            ).fetchone()
            if existing:
                investigation_id = existing["investigation_id"]
            else:
                investigation_id = LostAssetInvestigationService.create_in_transaction(
                    conn,
                    correction_id=correction_id,
                    finding_id=correction["finding_id"],
                    asset_id=correction["asset_id"],
                    actor=actor,
                    note=f"Opened from audit correction {correction_id}",
                )
        if owner and owner != actor:
            self.investigations.assign(investigation_id, owner=owner, actor=actor)
        item = self.investigations.require(investigation_id)
        return (
            f"Opened lost-asset investigation `{item.investigation_id}` for asset `{item.asset_id}`. "
            f"Owner: `{item.owner}` | Correction: `{item.correction_id}`."
        )

    def _list(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"lost investigations(?: (open|closed|all))?(?: owner (\S+))?", text, re.IGNORECASE)
        if not match:
            return None
        status, owner = match.groups()
        rows = self.investigations.list(status=(status or "open").lower(), owner=owner or "")
        if not rows:
            return "No matching lost-asset investigations."
        lines = ["*Lost-asset investigations*"]
        for item in rows[:50]:
            lines.append(
                f"• `{item.investigation_id}` — asset `{item.asset_id}` — *{item.status}* — "
                f"owner `{item.owner}` — outcome `{item.outcome or '—'}`"
            )
        return "\n".join(lines)

    def _assign(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"assign lost investigation (\S+) to (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        item = self.investigations.assign(match.group(1), owner=match.group(2), actor=actor)
        return f"Assigned lost-asset investigation `{item.investigation_id}` to `{item.owner}`."

    def _note(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"note lost investigation (\S+) (.+)", text, re.IGNORECASE)
        if not match:
            return None
        self.investigations.add_note(match.group(1), note=match.group(2), actor=actor)
        return f"Added note to lost-asset investigation `{match.group(1)}`."

    def _close(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"close lost investigation (\S+) outcome (found|confirmed_lost|transferred_custody|other) note (.+)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        investigation_id, outcome, note = match.groups()
        item = self.investigations.close(
            investigation_id,
            outcome=outcome,
            note=note,
            actor=actor,
        )
        return (
            f"Closed lost-asset investigation `{item.investigation_id}` with outcome "
            f"*{item.outcome}*."
        )

    def _history(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"lost investigation history (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        investigation_id = match.group(1)
        events = self.investigations.events(investigation_id)
        lines = [f"*Lost investigation history — {investigation_id}*"]
        for event in events:
            lines.append(
                f"• `{event.at.isoformat()}` — *{event.action}* by `{event.actor}` — {event.note}"
            )
        return "\n".join(lines)

    def _detail(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"lost investigation (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        item = self.investigations.require(match.group(1))
        return (
            f"*Lost-asset investigation {item.investigation_id}*\n"
            f"• Asset: `{item.asset_id}`\n"
            f"• Finding / correction: `{item.finding_id}` / `{item.correction_id}`\n"
            f"• Status: *{item.status}*\n"
            f"• Owner: `{item.owner}`\n"
            f"• Opened: `{item.opened_at.isoformat()}` by `{item.opened_by}`\n"
            f"• Outcome: `{item.outcome or '—'}`\n"
            f"• Closed: `{item.closed_at.isoformat() if item.closed_at else '—'}` by "
            f"`{item.closed_by or '—'}`"
        )

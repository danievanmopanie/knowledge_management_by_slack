"""Slack commands for durable serialized-asset audit findings."""

from __future__ import annotations

import re

from src.inventory.audit_findings import AssetAuditFindingService
from src.inventory.repository import InventoryRepository


class AssetAuditFindingCommandService:
    """Review and resolve audit findings without silently changing asset records."""

    def __init__(self, repository: InventoryRepository):
        self.findings = AssetAuditFindingService(repository)

    def execute_if_match(self, text: str, *, actor: str) -> str | None:
        handlers = (
            self._materialize,
            self._list,
            self._detail,
            self._history,
            self._resolve,
            self._reopen,
        )
        for handler in handlers:
            result = handler(text, actor)
            if result is not None:
                return result
        return None

    def _materialize(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"create audit findings (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        rows = self.findings.materialize(match.group(1), actor=actor)
        if not rows:
            return f"Audit `{match.group(1)}` has no discrepancies to materialize."
        return (
            f"Materialized *{len(rows)}* durable finding(s) from audit `{match.group(1)}`. "
            "No asset location or lifecycle state was changed."
        )

    def _list(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"audit findings(?: (open|resolved|all))?(?: audit (\S+))?",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        status, audit_id = match.groups()
        rows = self.findings.list(audit_id=audit_id or "", status=(status or "open").lower())
        if not rows:
            return "No matching asset-audit findings."
        lines = ["*Serialized-asset audit findings*"]
        for item in rows[:50]:
            identity = item.asset_id or item.observed_value
            location = ""
            if item.finding_type == "misplaced":
                location = f" — expected `{item.expected_location}`, observed `{item.observed_location}`"
            lines.append(
                f"• `{item.finding_id}` *{item.finding_type}* — `{identity}` — {item.status}{location}"
            )
        if len(rows) > 50:
            lines.append(f"… and {len(rows) - 50} more.")
        return "\n".join(lines)

    def _detail(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"audit finding (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        item = self.findings.require(match.group(1))
        return (
            f"*Audit finding {item.finding_id}*\n"
            f"• Audit: `{item.audit_id}`\n"
            f"• Type: *{item.finding_type}*\n"
            f"• Asset/value: `{item.asset_id or item.observed_value}`\n"
            f"• Expected location: `{item.expected_location or '—'}`\n"
            f"• Observed location: `{item.observed_location or '—'}`\n"
            f"• Status: *{item.status}*\n"
            f"• Resolution: `{item.resolution_type or '—'}`\n"
            f"• Note: {item.resolution_note or '—'}\n"
            "• Inventory changed by this finding: *no*"
        )

    def _history(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"audit finding history (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        finding_id = match.group(1)
        events = self.findings.events(finding_id)
        lines = [f"*Audit finding history — {finding_id}*"]
        for event in events:
            lines.append(
                f"• `{event.at.isoformat()}` — *{event.action}* by `{event.actor}` — {event.note}"
            )
        return "\n".join(lines)

    def _resolve(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"resolve audit finding (\S+) as (\S+) note (.+)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        finding_id, resolution_type, note = match.groups()
        item = self.findings.resolve(
            finding_id,
            resolution_type=resolution_type,
            note=note,
            actor=actor,
        )
        return (
            f"Resolved audit finding `{item.finding_id}` as *{item.resolution_type}*. "
            "This records the decision only; it does not silently change the asset record."
        )

    def _reopen(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"reopen audit finding (\S+) note (.+)", text, re.IGNORECASE)
        if not match:
            return None
        finding_id, note = match.groups()
        item = self.findings.reopen(finding_id, note=note, actor=actor)
        return f"Reopened audit finding `{item.finding_id}`."

"""Slack commands for serialized-asset physical audits."""

from __future__ import annotations

import re

from src.inventory.repository import InventoryRepository
from src.inventory.serialized_audit import AssetAuditResult, SerializedAssetAuditService


class AssetAuditCommandService:
    """Operate physical asset audits without automatically correcting inventory truth."""

    def __init__(self, repository: InventoryRepository):
        self.audits = SerializedAssetAuditService(repository)

    def execute_if_match(self, text: str, *, actor: str) -> str | None:
        handlers = (
            self._open,
            self._list_open,
            self._scan,
            self._scan_history,
            self._detail,
            self._close,
        )
        for handler in handlers:
            result = handler(text, actor)
            if result is not None:
                return result
        return None

    def _open(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"open asset audit (\S+)(?: note (.+))?",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        location_id, note = match.groups()
        audit = self.audits.open(location_id=location_id, actor=actor, note=note or "")
        result = self.audits.result(audit.audit_id)
        return (
            f"Opened serialized-asset audit `{audit.audit_id}` for `{audit.location_id}`.\n"
            f"Expected assets snapshot: *{result.expected_count}*.\n"
            f"Scan with `scan asset {audit.audit_id} <asset-id-or-serial>`; "
            f"close with `close asset audit {audit.audit_id}`."
        )

    def _list_open(self, text: str, actor: str) -> str | None:
        if text.lower() != "open asset audits":
            return None
        rows = self.audits.list_open()
        if not rows:
            return "No serialized-asset audits are currently open."
        lines = ["*Open serialized-asset audits*"]
        for audit in rows[:50]:
            result = self.audits.result(audit.audit_id)
            scanned = len(self.audits.scans(audit.audit_id))
            lines.append(
                f"• `{audit.audit_id}` @ `{audit.location_id}` — "
                f"expected {result.expected_count}, scans {scanned}, opened by `{audit.opened_by}`"
            )
        return "\n".join(lines)

    def _scan(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"scan asset (\S+) (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        audit_id, value = match.groups()
        scan = self.audits.scan(audit_id, value=value, actor=actor)
        if scan.result == "matched":
            return (
                f"Scan `{scan.scanned_value}` matched asset `{scan.asset_id}` at "
                f"`{scan.actual_location}`."
            )
        if scan.result == "misplaced":
            return (
                f"⚠️ Asset `{scan.asset_id}` was physically found at `{scan.actual_location}`, "
                f"but the ledger says `{scan.expected_location}`. No location was changed."
            )
        return (
            f"⚠️ Scan `{scan.scanned_value}` is not a known serialized asset. "
            "No inventory record was created or changed."
        )

    def _scan_history(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"asset audit scans (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        audit_id = match.group(1)
        rows = self.audits.scans(audit_id)
        if not rows:
            return f"Asset audit `{audit_id}` has no scans yet."
        lines = [f"*Asset audit scans — {audit_id}*"]
        for scan in rows:
            identity = scan.asset_id or scan.scanned_value
            location_note = ""
            if scan.result == "misplaced":
                location_note = (
                    f" — expected `{scan.expected_location}`, observed `{scan.actual_location}`"
                )
            lines.append(
                f"• `{identity}` — *{scan.result}*{location_note} — by `{scan.scanned_by}`"
            )
        return "\n".join(lines)

    def _detail(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"asset audit (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        audit = self.audits.require(match.group(1))
        result = self.audits.result(audit.audit_id)
        return self._format_result(result, status=audit.status)

    def _close(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"close asset audit (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        result = self.audits.close(match.group(1), actor=actor)
        return (
            self._format_result(result, status="closed")
            + "\nNo asset location or lifecycle state was changed automatically."
        )

    @staticmethod
    def _format_result(result: AssetAuditResult, *, status: str) -> str:
        lines = [
            f"*Asset audit {result.audit_id}*",
            f"• Location: `{result.location_id}`",
            f"• Status: *{status}*",
            f"• Expected: *{result.expected_count}*",
            f"• Matched: *{len(result.matched_asset_ids)}*",
            f"• Missing: *{len(result.missing_asset_ids)}*",
            f"• Misplaced: *{len(result.misplaced_asset_ids)}*",
            f"• Unknown scans: *{len(result.unexpected_values)}*",
        ]
        if result.missing_asset_ids:
            lines.append("• Missing assets: " + ", ".join(f"`{v}`" for v in result.missing_asset_ids))
        if result.misplaced_asset_ids:
            lines.append("• Misplaced assets: " + ", ".join(f"`{v}`" for v in result.misplaced_asset_ids))
        if result.unexpected_values:
            lines.append("• Unknown scans: " + ", ".join(f"`{v}`" for v in result.unexpected_values))
        return "\n".join(lines)

"""Slack-facing commands for serialized-asset lifecycle profiles and end-of-life evidence."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.inventory.asset_profile import AssetLifecycleProfile, AssetLifecycleProfileService
from src.inventory.domain import InventoryDomainError
from src.inventory.repository import InventoryRepository


class AssetProfileCommandService:
    """Parse lifecycle-profile commands without owning broader inventory operations."""

    def __init__(self, repository: InventoryRepository):
        self.profiles = AssetLifecycleProfileService(repository)

    def execute_if_match(self, text: str, *, actor: str) -> str | None:
        handlers = (
            self._asset_profile,
            self._set_asset_dates,
            self._out_of_warranty,
            self._assets_older_than,
            self._retirement_candidates,
            self._awaiting_disposal,
            self._add_disposal_evidence,
            self._disposal_evidence,
        )
        for handler in handlers:
            result = handler(text, actor)
            if result is not None:
                return result
        return None

    def _asset_profile(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"asset profile (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        profile = self.profiles.profile(match.group(1))
        return self._format_profile(profile)

    def _set_asset_dates(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"set asset dates (\S+)(?: received (\d{4}-\d{2}-\d{2}))?"
            r"(?: warranty (\d{4}-\d{2}-\d{2}))?",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        asset_id, received, warranty = match.groups()
        if not received and not warranty:
            raise InventoryDomainError("Supply `received YYYY-MM-DD`, `warranty YYYY-MM-DD`, or both.")
        profile = self.profiles.set_dates(
            asset_id,
            received_at=self._date(received) if received else None,
            warranty_end=self._date(warranty) if warranty else None,
        )
        return (
            f"Updated lifecycle dates for `{profile.asset_id}`. "
            f"Received: `{self._display_date(profile.received_at)}` | "
            f"Warranty end: `{self._display_date(profile.warranty_end)}`."
        )

    def _out_of_warranty(self, text: str, actor: str) -> str | None:
        if text.lower() != "out of warranty":
            return None
        rows = self.profiles.out_of_warranty()
        return self._format_report("Out-of-warranty serialized assets", rows)

    def _assets_older_than(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"assets older than ([0-9]+(?:\.[0-9]+)?) years?", text, re.IGNORECASE)
        if not match:
            return None
        years = float(match.group(1))
        rows = self.profiles.older_than(years)
        return self._format_report(f"Serialized assets aged {years:g}+ years", rows)

    def _retirement_candidates(self, text: str, actor: str) -> str | None:
        if text.lower() != "retirement candidates":
            return None
        rows = self.profiles.retirement_candidates()
        return self._format_report("Retirement candidates", rows)

    def _awaiting_disposal(self, text: str, actor: str) -> str | None:
        if text.lower() != "awaiting disposal":
            return None
        rows = self.profiles.awaiting_disposal()
        if not rows:
            return "No retired serialized assets are awaiting disposal."
        lines = ["*Retired assets awaiting disposal*"]
        for item in rows[:50]:
            evidence_count = len(self.profiles.disposal_evidence(item.asset_id))
            lines.append(
                f"• `{item.asset_id}` / `{item.serial_number}` — age {self._age(item)} — "
                f"disposal evidence: *{evidence_count}*"
            )
        if len(rows) > 50:
            lines.append(f"… and {len(rows) - 50} more.")
        return "\n".join(lines)

    def _add_disposal_evidence(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"add disposal evidence (\S+) type (\S+)(?: reference (\S+))?(?: note (.+))?",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        asset_id, evidence_type, reference, note = match.groups()
        evidence = self.profiles.add_disposal_evidence(
            asset_id,
            evidence_type=evidence_type,
            reference=reference or "",
            note=note or "",
            actor=actor,
        )
        return (
            f"Recorded *{evidence.evidence_type}* disposal evidence `{evidence.evidence_id}` "
            f"for asset `{evidence.asset_id}`."
        )

    def _disposal_evidence(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"disposal evidence (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        asset_id = match.group(1)
        rows = self.profiles.disposal_evidence(asset_id)
        if not rows:
            return f"Asset `{asset_id}` has no recorded disposal evidence."
        lines = [f"*Disposal evidence — {asset_id}*"]
        for item in rows:
            lines.append(
                f"• `{item.evidence_id}` *{item.evidence_type}* — reference "
                f"`{item.reference or '—'}` — {item.note or '—'} — "
                f"recorded by `{item.recorded_by}` on `{item.recorded_at.date().isoformat()}`"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_profile(profile: AssetLifecycleProfile) -> str:
        return (
            f"*Asset lifecycle profile — {profile.asset_id}*\n"
            f"• SKU / serial: `{profile.sku}` / `{profile.serial_number}`\n"
            f"• Status: *{profile.status.value}*\n"
            f"• Received: `{AssetProfileCommandService._display_date(profile.received_at)}`\n"
            f"• Age: *{AssetProfileCommandService._age(profile)}*\n"
            f"• Warranty end: `{AssetProfileCommandService._display_date(profile.warranty_end)}`\n"
            f"• Warranty: *{profile.warranty_status}*\n"
            f"• Retirement threshold: *{profile.retirement_age_years:g} years*\n"
            f"• Retirement eligible: *{'yes' if profile.retirement_eligible else 'no'}*\n"
            f"• Retired: `{AssetProfileCommandService._display_date(profile.retired_at)}`\n"
            f"• Disposed: `{AssetProfileCommandService._display_date(profile.disposed_at)}`"
        )

    def _format_report(self, title: str, rows: list[AssetLifecycleProfile]) -> str:
        if not rows:
            return f"No assets match: {title.lower()}."
        lines = [f"*{title}*"]
        for item in rows[:50]:
            lines.append(
                f"• `{item.asset_id}` / `{item.serial_number}` — `{item.sku}` — "
                f"{self._age(item)} — warranty *{item.warranty_status}* — status *{item.status.value}*"
            )
        if len(rows) > 50:
            lines.append(f"… and {len(rows) - 50} more.")
        return "\n".join(lines)

    @staticmethod
    def _age(profile: AssetLifecycleProfile) -> str:
        return f"{profile.age_years:.1f} years" if profile.age_years is not None else "unknown"

    @staticmethod
    def _display_date(value: datetime | None) -> str:
        return value.date().isoformat() if value else "—"

    @staticmethod
    def _date(value: str) -> datetime:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)

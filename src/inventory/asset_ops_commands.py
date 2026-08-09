"""Human-friendly serialized-asset lookup and inspection commands."""

from __future__ import annotations

import re

from src.inventory.asset_lifecycle import SerializedAssetLifecycleService
from src.inventory.domain import AssetLifecycle, InventoryDomainError, SerializedAsset
from src.inventory.locations import LocationService
from src.inventory.repository import InventoryRepository


class AssetOpsCommandService:
    """Focused commands discovered during the live Slack inventory pilot."""

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self.assets = SerializedAssetLifecycleService(repository)
        self.locations = LocationService(repository)

    def execute_if_match(self, text: str, *, actor: str) -> str | None:
        if match := re.fullmatch(r"status asset (\S+)", text, re.IGNORECASE):
            asset = self.repository.load_asset(match.group(1))
            if asset is None:
                raise InventoryDomainError(f"Unknown serialized asset: {match.group(1)}")
            return self._format_asset(asset)

        if match := re.fullmatch(r"asset serial (\S+)", text, re.IGNORECASE):
            return self._format_asset(self._asset_by_serial(match.group(1)))

        if match := re.fullmatch(
            r"inspect asset (\S+) ready at (\S+)(?: note (.+))?",
            text,
            re.IGNORECASE,
        ):
            asset_id, location_id, note = match.groups()
            location = self.locations.require_active(location_id)
            asset = self.assets.put_away(
                asset_id,
                location_id=location.location_id,
                actor=actor,
                note=note or "Returned-device inspection passed; ready for stock.",
            )
            return (
                f"Inspection passed for `{asset.asset_id}`. Status: *{asset.status.value}* "
                f"at `{asset.location_id}`."
            )

        if match := re.fullmatch(
            r"inspect asset (\S+) repair at (\S+)(?: note (.+))?",
            text,
            re.IGNORECASE,
        ):
            asset_id, location_id, note = match.groups()
            location = self.locations.require_active(location_id)
            asset = self.assets.send_to_repair(
                asset_id,
                actor=actor,
                location_id=location.location_id,
                note=note or "Returned-device inspection requires repair.",
            )
            return f"Inspection routed `{asset.asset_id}` to *repair* at `{asset.location_id}`."

        if match := re.fullmatch(
            r"inspect asset (\S+) quarantine at (\S+)(?: note (.+))?",
            text,
            re.IGNORECASE,
        ):
            asset_id, location_id, note = match.groups()
            location = self.locations.require_active(location_id)
            asset = self.assets.quarantine(
                asset_id,
                actor=actor,
                location_id=location.location_id,
                note=note or "Returned-device inspection requires quarantine.",
            )
            return f"Inspection routed `{asset.asset_id}` to *quarantine* at `{asset.location_id}`."

        return None

    def _asset_by_serial(self, serial_number: str) -> SerializedAsset:
        with self.repository._connect() as conn:
            rows = conn.execute(
                "SELECT asset_id FROM inventory_assets WHERE UPPER(serial_number)=UPPER(?)",
                (serial_number,),
            ).fetchall()
        if not rows:
            raise InventoryDomainError(f"Unknown serialized asset serial: {serial_number}")
        if len(rows) > 1:
            raise InventoryDomainError(
                f"Serial number {serial_number} is not unique; investigate the asset master before continuing."
            )
        asset = self.repository.load_asset(rows[0]["asset_id"])
        if asset is None:
            raise InventoryDomainError(f"Asset for serial {serial_number} could not be loaded.")
        return asset

    def _format_asset(self, asset: SerializedAsset) -> str:
        allocation = asset.allocation_type.value if asset.allocation_type else "none"
        due = asset.return_due_at.date().isoformat() if asset.return_due_at else "—"
        home = self.locations.get(asset.location_id) if asset.location_id else None
        home_text = (
            f"{home.location_id} ({home.name})"
            if home is not None
            else (asset.location_id or "—")
        )
        if asset.status == AssetLifecycle.ISSUED:
            physical = f"with `{asset.assigned_to or asset.customer_ref or 'customer'}`"
        elif asset.status == AssetLifecycle.RETURNED:
            physical = "returned; awaiting inspection / put-away"
        elif asset.status in {AssetLifecycle.REPAIR, AssetLifecycle.QUARANTINE}:
            physical = f"at `{home_text}`"
        elif asset.status in {AssetLifecycle.IN_STOCK, AssetLifecycle.RECEIVED}:
            physical = f"stored at `{home_text}`"
        elif asset.status == AssetLifecycle.LOST:
            physical = f"unknown — confirmed lost; last known location `{home_text}`"
        else:
            physical = f"last recorded home location `{home_text}`"
        return (
            f"*Asset {asset.asset_id}*\n"
            f"• SKU: `{asset.sku}`\n"
            f"• Serial: `{asset.serial_number}`\n"
            f"• Status: *{asset.status.value}*\n"
            f"• Home/storage location: `{home_text}`\n"
            f"• Physical custody: {physical}\n"
            f"• Assigned to: `{asset.assigned_to or '—'}`\n"
            f"• Customer: `{asset.customer_ref or '—'}`\n"
            f"• Allocation: `{allocation}`\n"
            f"• Return due: `{due}`"
        )

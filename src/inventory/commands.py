"""Deterministic command parsing for the Slack inventory channel."""

from __future__ import annotations

import re
from datetime import datetime

from src.inventory.asset_lifecycle import SerializedAssetLifecycleService
from src.inventory.domain import AllocationType, InventoryDomainError
from src.inventory.locations import LocationService
from src.inventory.quantity_stock import QuantityStockService
from src.inventory.repository import InventoryRepository


class InventoryCommandService:
    """Parse explicit Slack commands into deterministic inventory domain operations."""

    def __init__(self, repository: InventoryRepository | None = None):
        self.repository = repository or InventoryRepository()
        self.stock = QuantityStockService(self.repository)
        self.assets = SerializedAssetLifecycleService(self.repository)
        self.locations = LocationService(self.repository)

    def execute(self, message: str, *, actor: str) -> str:
        text = " ".join((message or "").strip().split())
        if not text:
            raise InventoryDomainError("Inventory command is empty.")

        handlers = (
            self._location_create,
            self._location_list,
            self._location_path,
            self._location_activate,
            self._location_deactivate,
            self._asset_status,
            self._stock_status,
            self._low_stock,
            self._asset_store,
            self._asset_issue,
            self._asset_return,
            self._asset_repair,
            self._asset_quarantine,
            self._asset_retire,
            self._asset_dispose,
            self._stock_reserve,
            self._stock_issue,
            self._stock_return,
            self._stock_transfer,
            self._stock_count,
        )
        for handler in handlers:
            result = handler(text, actor)
            if result is not None:
                return result
        raise InventoryDomainError("Unsupported inventory command. Type `help` for examples.")

    def _location_create(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"create location (\S+) type (\S+) site (\S+) name (.+?)(?: parent (\S+))?",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        location_id, location_type, site, name, parent = match.groups()
        location = self.locations.create(
            location_id=location_id,
            location_type=location_type,
            site=site,
            name=name,
            parent_id=parent or "",
            actor=actor,
        )
        parent_text = f" under `{location.parent_id}`" if location.parent_id else ""
        return (
            f"Created *{location.location_type}* `{location.location_id}`{parent_text} "
            f"at site `{location.site}` — {location.name}."
        )

    def _location_list(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"locations(?: site (\S+))?", text, re.IGNORECASE)
        if not match:
            return None
        site = match.group(1) or ""
        rows = self.locations.list(site=site)
        if not rows:
            return "No active inventory locations found."
        lines = ["*Inventory locations*"]
        for location in rows[:50]:
            parent = f" ← `{location.parent_id}`" if location.parent_id else ""
            lines.append(
                f"• `{location.location_id}` [{location.location_type}] — {location.name} "
                f"({location.site}){parent}"
            )
        if len(rows) > 50:
            lines.append(f"… and {len(rows) - 50} more.")
        return "\n".join(lines)

    def _location_path(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"location path (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        path = self.locations.path(match.group(1))
        return " → ".join(f"`{item.location_id}`" for item in path)

    def _location_activate(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"activate location (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        self.locations.set_active(match.group(1), active=True)
        return f"Activated inventory location `{match.group(1).upper()}`."

    def _location_deactivate(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"deactivate location (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        self.locations.set_active(match.group(1), active=False)
        return f"Deactivated inventory location `{match.group(1).upper()}`."

    def _asset_status(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"status asset (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        asset = self.repository.load_asset(match.group(1))
        if asset is None:
            raise InventoryDomainError(f"Unknown serialized asset: {match.group(1)}")
        allocation = asset.allocation_type.value if asset.allocation_type else "none"
        due = asset.return_due_at.date().isoformat() if asset.return_due_at else "—"
        location = asset.location_id or "—"
        location_record = self.locations.get(asset.location_id) if asset.location_id else None
        location_text = location
        if location_record:
            location_text = f"{location_record.location_id} ({location_record.name})"
        return (
            f"*Asset {asset.asset_id}*\n"
            f"• SKU: `{asset.sku}`\n"
            f"• Serial: `{asset.serial_number}`\n"
            f"• Status: *{asset.status.value}*\n"
            f"• Location: `{location_text}`\n"
            f"• Assigned to: `{asset.assigned_to or '—'}`\n"
            f"• Customer: `{asset.customer_ref or '—'}`\n"
            f"• Allocation: `{allocation}`\n"
            f"• Return due: `{due}`"
        )

    def _stock_status(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"stock (\S+) at (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        sku, location = match.groups()
        location_record = self.locations.require_active(location)
        on_hand = self.repository.stock_on_hand(sku, location_record.location_id)
        available = self.stock.available(sku, location_record.location_id)
        reserved = on_hand - available
        return (
            f"*Stock {sku} @ {location_record.location_id}*\n"
            f"• On hand: *{on_hand}*\n"
            f"• Reserved: *{reserved}*\n"
            f"• Available: *{available}*"
        )

    def _low_stock(self, text: str, actor: str) -> str | None:
        if text.lower() != "low stock":
            return None
        rows = self.stock.low_stock()
        if not rows:
            return "No configured stock items are currently at or below their reorder point."
        lines = ["*Low stock*"]
        for row in rows[:30]:
            available = int(row["on_hand"]) - int(row["reserved"])
            lines.append(
                f"• `{row['sku']}` @ `{row['location_id']}` — available {available}; "
                f"reorder point {row['reorder_point']}; suggested order {row['reorder_quantity']}"
            )
        return "\n".join(lines)

    def _asset_store(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"store asset (\S+) at (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        location = self.locations.require_active(match.group(2))
        asset = self.assets.put_away(match.group(1), location_id=location.location_id, actor=actor)
        return f"Stored `{asset.asset_id}` at `{asset.location_id}`. Status: *{asset.status.value}*."

    def _asset_issue(self, text: str, actor: str) -> str | None:
        loan = re.fullmatch(
            r"issue asset (\S+) to (\S+) customer (\S+) loan until (\d{4}-\d{2}-\d{2})",
            text,
            re.IGNORECASE,
        )
        if loan:
            asset_id, assignee, customer, due = loan.groups()
            asset = self.assets.issue(
                asset_id,
                assigned_to=assignee,
                customer_ref=customer,
                allocation_type=AllocationType.LOAN,
                return_due_at=datetime.fromisoformat(due),
                actor=actor,
            )
            return f"Issued `{asset.asset_id}` to `{assignee}` as a loan until `{due}`."

        dedicated = re.fullmatch(
            r"issue asset (\S+) to (\S+) customer (\S+) dedicated",
            text,
            re.IGNORECASE,
        )
        if not dedicated:
            return None
        asset_id, assignee, customer = dedicated.groups()
        asset = self.assets.issue(
            asset_id,
            assigned_to=assignee,
            customer_ref=customer,
            allocation_type=AllocationType.DEDICATED,
            actor=actor,
        )
        return f"Issued `{asset.asset_id}` to `{assignee}` as a dedicated asset."

    def _asset_return(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"return asset (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        asset = self.assets.return_asset(match.group(1), actor=actor)
        return f"Returned `{asset.asset_id}`. Status: *{asset.status.value}*."

    def _asset_repair(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"repair asset (\S+)(?: at (\S+))?", text, re.IGNORECASE)
        if not match:
            return None
        location_id = match.group(2) or ""
        if location_id:
            location_id = self.locations.require_active(location_id).location_id
        asset = self.assets.send_to_repair(match.group(1), actor=actor, location_id=location_id)
        return f"Moved `{asset.asset_id}` to *repair*."

    def _asset_quarantine(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"quarantine asset (\S+)(?: at (\S+))?", text, re.IGNORECASE)
        if not match:
            return None
        location_id = match.group(2) or ""
        if location_id:
            location_id = self.locations.require_active(location_id).location_id
        asset = self.assets.quarantine(match.group(1), actor=actor, location_id=location_id)
        return f"Moved `{asset.asset_id}` to *quarantine*."

    def _asset_retire(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"retire asset (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        asset = self.assets.retire(match.group(1), actor=actor)
        return f"Retired `{asset.asset_id}`."

    def _asset_dispose(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"dispose asset (\S+)", text, re.IGNORECASE)
        if not match:
            return None
        asset = self.assets.dispose(match.group(1), actor=actor)
        return f"Disposed `{asset.asset_id}`."

    def _stock_reserve(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"reserve stock (\S+) (\d+) at (\S+) for (\S+)", text, re.IGNORECASE
        )
        if not match:
            return None
        sku, quantity, location_id, customer = match.groups()
        location = self.locations.require_active(location_id)
        reservation = self.stock.reserve(
            sku=sku,
            location_id=location.location_id,
            quantity=int(quantity),
            customer_ref=customer,
            requested_by=actor,
        )
        return (
            f"Reserved *{quantity}* × `{sku}` at `{location.location_id}` for `{customer}`.\n"
            f"Reservation: `{reservation.reservation_id}`"
        )

    def _stock_issue(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"issue stock (\S+) (\d+) from (\S+) to (\S+)(?: reservation (\S+))?",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        sku, quantity, location_id, customer, reservation = match.groups()
        location = self.locations.require_active(location_id)
        tx = self.stock.issue(
            sku=sku,
            location_id=location.location_id,
            quantity=int(quantity),
            customer_ref=customer,
            actor=actor,
            reservation_id=reservation or "",
        )
        return (
            f"Issued *{quantity}* × `{sku}` from `{location.location_id}` to `{customer}`. "
            f"Transaction `{tx}`."
        )

    def _stock_return(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"return stock (\S+) (\d+) to (\S+) from (\S+)", text, re.IGNORECASE
        )
        if not match:
            return None
        sku, quantity, location_id, customer = match.groups()
        location = self.locations.require_active(location_id)
        tx = self.stock.return_stock(
            sku=sku,
            location_id=location.location_id,
            quantity=int(quantity),
            customer_ref=customer,
            actor=actor,
        )
        return f"Returned *{quantity}* × `{sku}` to `{location.location_id}`. Transaction `{tx}`."

    def _stock_transfer(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(
            r"transfer stock (\S+) (\d+) from (\S+) to (\S+)", text, re.IGNORECASE
        )
        if not match:
            return None
        sku, quantity, source_id, destination_id = match.groups()
        source = self.locations.require_active(source_id)
        destination = self.locations.require_active(destination_id)
        tx = self.stock.transfer(
            sku=sku,
            from_location=source.location_id,
            to_location=destination.location_id,
            quantity=int(quantity),
            actor=actor,
        )
        return (
            f"Transferred *{quantity}* × `{sku}` from `{source.location_id}` to "
            f"`{destination.location_id}`. Transaction `{tx}`."
        )

    def _stock_count(self, text: str, actor: str) -> str | None:
        match = re.fullmatch(r"count stock (\S+) at (\S+) = (\d+)", text, re.IGNORECASE)
        if not match:
            return None
        sku, location_id, counted = match.groups()
        location = self.locations.require_active(location_id)
        result = self.stock.count_and_reconcile(
            sku=sku,
            location_id=location.location_id,
            counted_quantity=int(counted),
            actor=actor,
        )
        return (
            f"Counted `{sku}` at `{location.location_id}`: expected *{result.expected}*, "
            f"counted *{result.counted}*, variance *{result.variance:+d}*."
        )

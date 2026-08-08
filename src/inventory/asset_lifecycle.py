"""Transactional lifecycle service for serialized inventory assets."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from src.inventory.customers import CustomerCustodyService
from src.inventory.domain import (
    AllocationType,
    AssetLifecycle,
    AssetMovement,
    InventoryDomainError,
    SerializedAsset,
)
from src.inventory.repository import InventoryRepository


ALLOWED_TRANSITIONS: dict[AssetLifecycle, set[AssetLifecycle]] = {
    AssetLifecycle.ORDERED: {AssetLifecycle.RECEIVED},
    AssetLifecycle.RECEIVED: {AssetLifecycle.IN_STOCK, AssetLifecycle.QUARANTINE},
    AssetLifecycle.IN_STOCK: {
        AssetLifecycle.ISSUED,
        AssetLifecycle.REPAIR,
        AssetLifecycle.QUARANTINE,
        AssetLifecycle.RETIRED,
    },
    AssetLifecycle.ISSUED: {AssetLifecycle.RETURNED, AssetLifecycle.QUARANTINE},
    AssetLifecycle.RETURNED: {
        AssetLifecycle.IN_STOCK,
        AssetLifecycle.REPAIR,
        AssetLifecycle.QUARANTINE,
        AssetLifecycle.RETIRED,
    },
    AssetLifecycle.REPAIR: {
        AssetLifecycle.IN_STOCK,
        AssetLifecycle.QUARANTINE,
        AssetLifecycle.RETIRED,
    },
    AssetLifecycle.QUARANTINE: {
        AssetLifecycle.IN_STOCK,
        AssetLifecycle.REPAIR,
        AssetLifecycle.RETIRED,
    },
    AssetLifecycle.RETIRED: {AssetLifecycle.DISPOSED},
    AssetLifecycle.DISPOSED: set(),
}


class SerializedAssetLifecycleService:
    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self.customers = CustomerCustodyService(repository)

    def put_away(self, asset_id: str, *, location_id: str, actor: str, note: str = "") -> SerializedAsset:
        if not location_id:
            raise InventoryDomainError("Storage location is required.")
        return self._transition(
            asset_id,
            AssetLifecycle.IN_STOCK,
            actor=actor,
            location_id=location_id,
            clear_assignment=True,
            note=note,
        )

    def issue(
        self,
        asset_id: str,
        *,
        assigned_to: str,
        customer_ref: str,
        allocation_type: AllocationType,
        actor: str,
        return_due_at: datetime | None = None,
        note: str = "",
    ) -> SerializedAsset:
        if not assigned_to:
            raise InventoryDomainError("Assignee is required to issue an asset.")
        if not customer_ref:
            raise InventoryDomainError("Customer reference is required to issue an asset.")
        customer = self.customers.require_active(customer_ref)
        if allocation_type == AllocationType.LOAN and return_due_at is None:
            raise InventoryDomainError("Loan allocation requires a return due date.")
        if allocation_type == AllocationType.DEDICATED and return_due_at is not None:
            raise InventoryDomainError("Dedicated allocation must not have a return due date.")
        return self._transition(
            asset_id,
            AssetLifecycle.ISSUED,
            actor=actor,
            assigned_to=assigned_to,
            customer_ref=customer.customer_id,
            allocation_type=allocation_type,
            return_due_at=return_due_at,
            note=note,
        )

    def return_asset(self, asset_id: str, *, actor: str, note: str = "") -> SerializedAsset:
        return self._transition(
            asset_id,
            AssetLifecycle.RETURNED,
            actor=actor,
            clear_assignment=True,
            note=note,
        )

    def send_to_repair(self, asset_id: str, *, actor: str, location_id: str = "", note: str = "") -> SerializedAsset:
        return self._transition(
            asset_id,
            AssetLifecycle.REPAIR,
            actor=actor,
            location_id=location_id or None,
            clear_assignment=True,
            note=note,
        )

    def quarantine(self, asset_id: str, *, actor: str, location_id: str = "", note: str = "") -> SerializedAsset:
        return self._transition(
            asset_id,
            AssetLifecycle.QUARANTINE,
            actor=actor,
            location_id=location_id or None,
            clear_assignment=True,
            note=note,
        )

    def retire(self, asset_id: str, *, actor: str, note: str = "") -> SerializedAsset:
        return self._transition(
            asset_id,
            AssetLifecycle.RETIRED,
            actor=actor,
            clear_assignment=True,
            retired_at=datetime.now(timezone.utc),
            note=note,
        )

    def dispose(self, asset_id: str, *, actor: str, note: str = "") -> SerializedAsset:
        return self._transition(
            asset_id,
            AssetLifecycle.DISPOSED,
            actor=actor,
            clear_assignment=True,
            disposed_at=datetime.now(timezone.utc),
            note=note,
        )

    def _transition(
        self,
        asset_id: str,
        target: AssetLifecycle,
        *,
        actor: str,
        location_id: str | None = None,
        assigned_to: str | None = None,
        customer_ref: str | None = None,
        allocation_type: AllocationType | None = None,
        return_due_at: datetime | None = None,
        clear_assignment: bool = False,
        retired_at: datetime | None = None,
        disposed_at: datetime | None = None,
        note: str = "",
    ) -> SerializedAsset:
        with self.repository.transaction() as conn:
            asset = self.repository.load_asset(asset_id, conn)
            if asset is None:
                raise InventoryDomainError(f"Unknown serialized asset: {asset_id}")
            if target not in ALLOWED_TRANSITIONS.get(asset.status, set()):
                allowed = ", ".join(sorted(item.value for item in ALLOWED_TRANSITIONS.get(asset.status, set())))
                raise InventoryDomainError(
                    f"Cannot move {asset.asset_id} from {asset.status.value} to {target.value}. "
                    f"Allowed: {allowed or 'none'}."
                )

            updates = {"status": target}
            if location_id is not None:
                updates["location_id"] = location_id
            if assigned_to is not None:
                updates["assigned_to"] = assigned_to
            if customer_ref is not None:
                updates["customer_ref"] = customer_ref
            if allocation_type is not None:
                updates["allocation_type"] = allocation_type
            if return_due_at is not None:
                updates["return_due_at"] = return_due_at
            if clear_assignment:
                updates.update(
                    assigned_to="",
                    customer_ref="",
                    allocation_type=None,
                    return_due_at=None,
                )
            if retired_at is not None:
                updates["retired_at"] = retired_at
            if disposed_at is not None:
                updates["disposed_at"] = disposed_at

            updated = replace(asset, **updates)
            movement = AssetMovement(
                movement_id=str(uuid4()),
                asset_id=asset.asset_id,
                from_status=asset.status,
                to_status=target,
                actor=actor,
                from_location=asset.location_id,
                to_location=updated.location_id,
                customer_ref=updated.customer_ref or asset.customer_ref,
                note=note,
            )
            self.repository.save_asset(updated, conn)
            self.repository.save_asset_movement(movement, conn)
            return updated

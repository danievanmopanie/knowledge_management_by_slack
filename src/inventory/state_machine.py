"""Hard lifecycle state machine for inventory assets."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from src.inventory.models import Asset, AssetStatus, Movement

# Allowed edges: from_status -> set of to_status
ALLOWED: dict[AssetStatus, set[AssetStatus]] = {
    AssetStatus.ORDERED: {AssetStatus.RECEIVED},
    AssetStatus.RECEIVED: {AssetStatus.IN_STOREROOM, AssetStatus.QUARANTINE},
    AssetStatus.IN_STOREROOM: {
        AssetStatus.ISSUED_DEDICATED,
        AssetStatus.ISSUED_LOAN,
        AssetStatus.QUARANTINE,
        AssetStatus.RETIRED,
    },
    AssetStatus.ISSUED_DEDICATED: {AssetStatus.RETURNED},
    AssetStatus.ISSUED_LOAN: {AssetStatus.RETURNED, AssetStatus.QUARANTINE},
    AssetStatus.RETURNED: {
        AssetStatus.IN_STOREROOM,
        AssetStatus.QUARANTINE,
        AssetStatus.RETIRED,
    },
    AssetStatus.QUARANTINE: {
        AssetStatus.IN_STOREROOM,
        AssetStatus.ISSUED_DEDICATED,
        AssetStatus.RETIRED,
    },
    AssetStatus.RETIRED: set(),
}


class TransitionError(Exception):
    """Raised when a transition is illegal or missing required data."""

    def __init__(self, message: str, *, missing_step: str | None = None):
        super().__init__(message)
        self.missing_step = missing_step


def can_transition(asset: Asset, to: AssetStatus) -> bool:
    return to in ALLOWED.get(asset.status, set())


def transition(
    asset: Asset,
    to: AssetStatus,
    *,
    actor: str,
    location: str | None = None,
    assignee: str | None = None,
    return_due_at: datetime | None = None,
    note: str = "",
    photo_ref: str = "",
) -> tuple[Asset, Movement]:
    """
    Apply a legal transition or raise TransitionError.

    Returns updated asset + movement log entry (caller persists both).
    """
    if not can_transition(asset, to):
        allowed = ", ".join(s.value for s in sorted(ALLOWED.get(asset.status, set()), key=lambda x: x.value))
        raise TransitionError(
            f"Cannot move {asset.asset_id} from {asset.status.value} to {to.value}. "
            f"Allowed: {allowed or 'none'}.",
            missing_step=_suggest_step(asset, to),
        )

    # Extra guards
    if to == AssetStatus.IN_STOREROOM and not (location or asset.location):
        raise TransitionError(
            "Location required to put asset in storeroom.",
            missing_step=f"/store {asset.asset_id} <location>",
        )

    if to == AssetStatus.ISSUED_LOAN and return_due_at is None:
        raise TransitionError(
            "Loan requires an expected return date.",
            missing_step=f"/issue {asset.asset_id} to @user loan until YYYY-MM-DD",
        )

    if to in {AssetStatus.ISSUED_DEDICATED, AssetStatus.ISSUED_LOAN} and not assignee:
        raise TransitionError(
            "Assignee required to issue asset.",
            missing_step=f"/issue {asset.asset_id} to @user …",
        )

    if to == AssetStatus.RETIRED and asset.status in {
        AssetStatus.ISSUED_DEDICATED,
        AssetStatus.ISSUED_LOAN,
    }:
        raise TransitionError(
            "Return the asset before retiring it.",
            missing_step=f"/return {asset.asset_id}",
        )

    from_status = asset.status
    updates: dict = {"status": to}
    if location:
        updates["location"] = location
    if assignee is not None:
        updates["assigned_to"] = assignee
    if to == AssetStatus.ISSUED_LOAN:
        updates["return_due_at"] = return_due_at
    if to in {AssetStatus.RETURNED, AssetStatus.IN_STOREROOM, AssetStatus.RETIRED}:
        if to != AssetStatus.ISSUED_LOAN:
            updates.setdefault("assigned_to", "" if to != AssetStatus.ISSUED_DEDICATED else asset.assigned_to)
    if to in {AssetStatus.IN_STOREROOM, AssetStatus.RETURNED}:
        updates["assigned_to"] = ""

    new_asset = replace(asset, **updates)
    movement = Movement(
        asset_id=asset.asset_id,
        from_status=from_status.value,
        to_status=to.value,
        actor=actor,
        at=datetime.now(timezone.utc),
        note=note,
        photo_ref=photo_ref,
    )
    return new_asset, movement


def _suggest_step(asset: Asset, requested: AssetStatus) -> str:
    if asset.status == AssetStatus.ORDERED and requested != AssetStatus.RECEIVED:
        return f"Receive against a PO first (barcode + /deliver)"
    if asset.status == AssetStatus.RECEIVED and requested in {
        AssetStatus.ISSUED_DEDICATED,
        AssetStatus.ISSUED_LOAN,
    }:
        return f"/store {asset.asset_id} <location> before issuing"
    if requested == AssetStatus.RETIRED and asset.status in {
        AssetStatus.ISSUED_DEDICATED,
        AssetStatus.ISSUED_LOAN,
    }:
        return f"/return {asset.asset_id} before retire"
    return "Check /status for legal next actions"

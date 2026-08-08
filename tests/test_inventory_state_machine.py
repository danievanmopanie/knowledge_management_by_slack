"""Regression tests for deterministic inventory lifecycle rules."""

from datetime import datetime, timezone

import pytest

from src.inventory.models import Asset, AssetStatus
from src.inventory.state_machine import TransitionError, transition


def test_ordered_asset_can_be_received() -> None:
    asset = Asset(asset_id="A-100", status=AssetStatus.ORDERED)

    updated, movement = transition(asset, AssetStatus.RECEIVED, actor="U123")

    assert updated.status is AssetStatus.RECEIVED
    assert movement.from_status == AssetStatus.ORDERED.value
    assert movement.to_status == AssetStatus.RECEIVED.value
    assert movement.actor == "U123"


def test_ordered_asset_cannot_skip_directly_to_issue() -> None:
    asset = Asset(asset_id="A-100", status=AssetStatus.ORDERED)

    with pytest.raises(TransitionError) as exc:
        transition(
            asset,
            AssetStatus.ISSUED_DEDICATED,
            actor="U123",
            assignee="U999",
        )

    assert "Cannot move" in str(exc.value)
    assert exc.value.missing_step is not None
    assert "Receive" in exc.value.missing_step


def test_loan_requires_expected_return_date() -> None:
    asset = Asset(
        asset_id="A-100",
        status=AssetStatus.IN_STOREROOM,
        location="Shelf-B3",
    )

    with pytest.raises(TransitionError) as exc:
        transition(
            asset,
            AssetStatus.ISSUED_LOAN,
            actor="U123",
            assignee="U999",
        )

    assert "expected return date" in str(exc.value)


def test_valid_loan_records_assignee_and_due_date() -> None:
    asset = Asset(
        asset_id="A-100",
        status=AssetStatus.IN_STOREROOM,
        location="Shelf-B3",
    )
    due = datetime(2026, 8, 20, tzinfo=timezone.utc)

    updated, movement = transition(
        asset,
        AssetStatus.ISSUED_LOAN,
        actor="U123",
        assignee="U999",
        return_due_at=due,
    )

    assert updated.status is AssetStatus.ISSUED_LOAN
    assert updated.assigned_to == "U999"
    assert updated.return_due_at == due
    assert movement.to_status == AssetStatus.ISSUED_LOAN.value

from datetime import datetime, timezone

import pytest

from src.inventory.asset_lifecycle import SerializedAssetLifecycleService
from src.inventory.domain import (
    AllocationType,
    AssetLifecycle,
    InventoryDomainError,
    SerializedAsset,
)
from src.inventory.repository import InventoryRepository


def seed_asset(repo, *, asset_id="A-100", status=AssetLifecycle.RECEIVED, location="RECV"):
    asset = SerializedAsset(
        asset_id=asset_id,
        sku="LAPTOP-01",
        serial_number=f"SER-{asset_id}",
        status=status,
        location_id=location,
        purchase_order_id="PO-1",
    )
    with repo.transaction() as conn:
        repo.save_asset(asset, conn)
    return asset


def seed_customer(service, customer_id="EMP-42"):
    return service.customers.create(
        customer_id=customer_id,
        name=f"Customer {customer_id}",
        customer_type="employee",
        actor="admin",
    )


def test_full_serialized_asset_lifecycle(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = SerializedAssetLifecycleService(repo)
    seed_asset(repo)
    seed_customer(service)

    asset = service.put_away("A-100", location_id="STORE-A/SHELF-1", actor="U1")
    assert asset.status == AssetLifecycle.IN_STOCK

    due = datetime(2026, 8, 20, tzinfo=timezone.utc)
    asset = service.issue(
        "A-100",
        assigned_to="U-CUSTOMER",
        customer_ref="EMP-42",
        allocation_type=AllocationType.LOAN,
        return_due_at=due,
        actor="U1",
    )
    assert asset.status == AssetLifecycle.ISSUED
    assert asset.return_due_at == due

    asset = service.return_asset("A-100", actor="U1")
    assert asset.status == AssetLifecycle.RETURNED
    assert asset.assigned_to == ""
    assert asset.return_due_at is None

    asset = service.send_to_repair("A-100", actor="U1", location_id="REPAIR-CAGE")
    assert asset.status == AssetLifecycle.REPAIR

    asset = service.put_away("A-100", location_id="STORE-A/SHELF-2", actor="U1")
    assert asset.status == AssetLifecycle.IN_STOCK

    asset = service.retire("A-100", actor="U1", note="End of useful life")
    assert asset.status == AssetLifecycle.RETIRED
    assert asset.retired_at is not None

    asset = service.dispose("A-100", actor="U1", note="Certified recycler")
    assert asset.status == AssetLifecycle.DISPOSED
    assert asset.disposed_at is not None

    persisted = repo.load_asset("A-100")
    assert persisted is not None
    assert persisted.status == AssetLifecycle.DISPOSED
    assert len(repo.asset_movements("A-100")) == 7


def test_loan_requires_due_date_and_dedicated_rejects_due_date(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = SerializedAssetLifecycleService(repo)
    seed_asset(repo)
    seed_customer(service, "EMP-2")
    service.put_away("A-100", location_id="STORE-A", actor="U1")

    with pytest.raises(InventoryDomainError, match="return due date"):
        service.issue(
            "A-100",
            assigned_to="U2",
            customer_ref="EMP-2",
            allocation_type=AllocationType.LOAN,
            actor="U1",
        )

    with pytest.raises(InventoryDomainError, match="must not have"):
        service.issue(
            "A-100",
            assigned_to="U2",
            customer_ref="EMP-2",
            allocation_type=AllocationType.DEDICATED,
            return_due_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            actor="U1",
        )


def test_cannot_dispose_issued_asset_or_skip_retirement(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = SerializedAssetLifecycleService(repo)
    seed_asset(repo)
    seed_customer(service, "EMP-2")
    service.put_away("A-100", location_id="STORE-A", actor="U1")
    service.issue(
        "A-100",
        assigned_to="U2",
        customer_ref="EMP-2",
        allocation_type=AllocationType.DEDICATED,
        actor="U1",
    )

    with pytest.raises(InventoryDomainError, match="Cannot move"):
        service.retire("A-100", actor="U1")
    with pytest.raises(InventoryDomainError, match="Cannot move"):
        service.dispose("A-100", actor="U1")

    assert repo.load_asset("A-100").status == AssetLifecycle.ISSUED


def test_unknown_customer_is_rejected_before_issue(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = SerializedAssetLifecycleService(repo)
    seed_asset(repo)
    service.put_away("A-100", location_id="STORE-A", actor="U1")

    with pytest.raises(InventoryDomainError, match="Unknown inventory customer"):
        service.issue(
            "A-100",
            assigned_to="U2",
            customer_ref="MADE-UP",
            allocation_type=AllocationType.DEDICATED,
            actor="U1",
        )


def test_transition_and_movement_write_are_atomic(tmp_path, monkeypatch):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = SerializedAssetLifecycleService(repo)
    seed_asset(repo)

    def fail(*args, **kwargs):
        raise RuntimeError("movement write failed")

    monkeypatch.setattr(repo, "save_asset_movement", fail)

    with pytest.raises(RuntimeError, match="movement write failed"):
        service.put_away("A-100", location_id="STORE-A", actor="U1")

    persisted = repo.load_asset("A-100")
    assert persisted.status == AssetLifecycle.RECEIVED
    assert repo.asset_movements("A-100") == []

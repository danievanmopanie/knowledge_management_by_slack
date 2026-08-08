from datetime import datetime, timedelta, timezone

import pytest

from src.inventory.asset_lifecycle import SerializedAssetLifecycleService
from src.inventory.customers import CustomerCustodyService
from src.inventory.domain import AllocationType, AssetLifecycle, InventoryDomainError, SerializedAsset
from src.inventory.repository import InventoryRepository


def seed_asset(repo, asset_id="A-1"):
    with repo.transaction() as conn:
        repo.save_asset(
            SerializedAsset(
                asset_id=asset_id,
                sku="LAPTOP-01",
                serial_number=f"SER-{asset_id}",
                status=AssetLifecycle.IN_STOCK,
                location_id="STORE-A",
                purchase_order_id="PO-1",
            ),
            conn,
        )


def test_customer_create_list_and_deactivate(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = CustomerCustodyService(repo)

    customer = service.create(
        customer_id="emp-42",
        name="Jane Smith",
        customer_type="employee",
        site="SITE-A",
        email="jane@example.com",
        actor="admin",
    )
    assert customer.customer_id == "EMP-42"
    assert customer.active is True
    assert [item.customer_id for item in service.list()] == ["EMP-42"]

    customer = service.set_active("EMP-42", active=False)
    assert customer.active is False
    assert service.list() == []
    assert service.list(include_inactive=True)[0].customer_id == "EMP-42"


def test_customer_cannot_be_deactivated_while_asset_is_issued(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    customers = CustomerCustodyService(repo)
    lifecycle = SerializedAssetLifecycleService(repo)
    customers.create(
        customer_id="EMP-42",
        name="Jane Smith",
        customer_type="employee",
        actor="admin",
    )
    seed_asset(repo)
    lifecycle.issue(
        "A-1",
        assigned_to="U123",
        customer_ref="EMP-42",
        allocation_type=AllocationType.DEDICATED,
        actor="U1",
    )

    with pytest.raises(InventoryDomainError, match="still issued"):
        customers.set_active("EMP-42", active=False)


def test_customer_current_assets_overdue_and_custody_history(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    customers = CustomerCustodyService(repo)
    lifecycle = SerializedAssetLifecycleService(repo)
    customers.create(
        customer_id="EMP-42",
        name="Jane Smith",
        customer_type="employee",
        actor="admin",
    )
    seed_asset(repo)
    due = datetime.now(timezone.utc) - timedelta(days=3)
    lifecycle.issue(
        "A-1",
        assigned_to="U123",
        customer_ref="EMP-42",
        allocation_type=AllocationType.LOAN,
        return_due_at=due,
        actor="U1",
    )

    assets = customers.current_assets("EMP-42")
    assert len(assets) == 1
    assert assets[0]["asset_id"] == "A-1"
    overdue = customers.overdue_assets("EMP-42")
    assert len(overdue) == 1
    assert overdue[0]["asset_id"] == "A-1"
    history = customers.custody_history("EMP-42")
    assert len(history) == 1
    assert history[0]["to_status"] == "issued"

    lifecycle.return_asset("A-1", actor="U1")
    assert customers.current_assets("EMP-42") == []
    history = customers.custody_history("EMP-42")
    assert len(history) == 2
    assert history[0]["to_status"] == "returned"

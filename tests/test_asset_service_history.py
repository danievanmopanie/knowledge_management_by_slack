import pytest

from src.inventory.domain import AssetLifecycle, InventoryDomainError, SerializedAsset
from src.inventory.repository import InventoryRepository
from src.inventory.service_history import AssetServiceHistoryService
from src.inventory.suppliers import SupplierService


def seed_asset(repo, *, asset_id="A-1", status=AssetLifecycle.REPAIR):
    with repo.transaction() as conn:
        repo.save_asset(
            SerializedAsset(
                asset_id=asset_id,
                sku="LAPTOP-01",
                serial_number=f"SER-{asset_id}",
                status=status,
                location_id="REPAIR-CAGE",
                purchase_order_id="PO-1",
            ),
            conn,
        )


def seed_supplier(repo, supplier_id="DELL-SA"):
    SupplierService(repo).create(
        supplier_id=supplier_id,
        name="Dell South Africa",
        actor="admin",
    )


def test_open_and_complete_rma_case_with_cost_and_history(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo)
    seed_supplier(repo)
    service = AssetServiceHistoryService(repo)

    case = service.open_case(
        asset_id="A-1",
        service_type="rma",
        supplier_id="DELL-SA",
        external_reference="RMA-123",
        actor="U1",
        note="No power",
    )
    assert case.status == "open"
    assert case.supplier_id == "DELL-SA"
    assert case.external_reference == "RMA-123"

    completed = service.complete(
        case.service_id,
        actor="U2",
        outcome="mainboard_replaced",
        cost=1250.50,
        note="Passed burn-in test",
    )
    assert completed.status == "completed"
    assert completed.cost == 1250.50
    assert completed.outcome == "mainboard_replaced"
    assert service.total_service_cost("A-1") == 1250.50

    events = service.events(case.service_id)
    assert [event.action for event in events] == ["opened", "completed"]
    assert events[1].actor == "U2"


def test_only_one_open_service_case_per_asset(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo)
    service = AssetServiceHistoryService(repo)

    first = service.open_case(
        asset_id="A-1",
        service_type="repair",
        actor="U1",
    )
    with pytest.raises(InventoryDomainError, match="already has an open service case"):
        service.open_case(
            asset_id="A-1",
            service_type="inspection",
            actor="U2",
        )

    service.cancel(first.service_id, actor="U1", note="Repair no longer required")
    second = service.open_case(
        asset_id="A-1",
        service_type="inspection",
        actor="U2",
    )
    assert second.status == "open"


def test_repair_case_requires_serviceable_asset_status(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo, status=AssetLifecycle.IN_STOCK)
    service = AssetServiceHistoryService(repo)

    with pytest.raises(InventoryDomainError, match="repair or quarantine"):
        service.open_case(
            asset_id="A-1",
            service_type="repair",
            actor="U1",
        )

    inspection = service.open_case(
        asset_id="A-1",
        service_type="inspection",
        actor="U1",
    )
    assert inspection.service_type == "inspection"


def test_rma_requires_active_supplier_but_existing_case_can_complete_after_deactivation(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo)
    seed_supplier(repo)
    suppliers = SupplierService(repo)
    service = AssetServiceHistoryService(repo)

    case = service.open_case(
        asset_id="A-1",
        service_type="rma",
        supplier_id="DELL-SA",
        actor="U1",
    )
    suppliers.set_active("DELL-SA", active=False)

    completed = service.complete(
        case.service_id,
        actor="U2",
        outcome="repaired",
    )
    assert completed.status == "completed"

    seed_asset(repo, asset_id="A-2")
    with pytest.raises(InventoryDomainError, match="inactive"):
        service.open_case(
            asset_id="A-2",
            service_type="rma",
            supplier_id="DELL-SA",
            actor="U1",
        )


def test_disposed_asset_cannot_open_service_case(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo, status=AssetLifecycle.DISPOSED)
    service = AssetServiceHistoryService(repo)

    with pytest.raises(InventoryDomainError, match="Disposed assets"):
        service.open_case(
            asset_id="A-1",
            service_type="inspection",
            actor="U1",
        )

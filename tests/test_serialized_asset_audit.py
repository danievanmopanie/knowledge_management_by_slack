import pytest

from src.inventory.domain import AssetLifecycle, InventoryDomainError, SerializedAsset
from src.inventory.locations import LocationService
from src.inventory.repository import InventoryRepository
from src.inventory.serialized_audit import SerializedAssetAuditService


def seed_locations(repo):
    locations = LocationService(repo)
    locations.create(
        location_id="SITE-A",
        location_type="site",
        site="SITE-A",
        name="Main Site",
        actor="admin",
    )
    locations.create(
        location_id="STORE-A",
        location_type="storeroom",
        site="SITE-A",
        name="Main Store",
        parent_id="SITE-A",
        actor="admin",
    )
    locations.create(
        location_id="STORE-B",
        location_type="storeroom",
        site="SITE-A",
        name="Other Store",
        parent_id="SITE-A",
        actor="admin",
    )


def seed_asset(repo, asset_id, location, status=AssetLifecycle.IN_STOCK):
    with repo.transaction() as conn:
        repo.save_asset(
            SerializedAsset(
                asset_id=asset_id,
                sku="LAPTOP-01",
                serial_number=f"SER-{asset_id}",
                status=status,
                location_id=location,
                purchase_order_id="PO-1",
            ),
            conn,
        )


def test_audit_snapshots_expected_and_reconciles_matched_missing_misplaced_unknown(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_locations(repo)
    seed_asset(repo, "A-1", "STORE-A")
    seed_asset(repo, "A-2", "STORE-A")
    seed_asset(repo, "A-3", "STORE-B")
    service = SerializedAssetAuditService(repo)

    audit = service.open(location_id="STORE-A", actor="U1")
    assert service.result(audit.audit_id).expected_count == 2

    matched = service.scan(audit.audit_id, value="A-1", actor="U1")
    assert matched.result == "matched"
    assert matched.expected_location == "STORE-A"
    assert matched.actual_location == "STORE-A"

    misplaced = service.scan(audit.audit_id, value="SER-A-3", actor="U1")
    assert misplaced.result == "misplaced"
    assert misplaced.expected_location == "STORE-B"
    assert misplaced.actual_location == "STORE-A"

    unknown = service.scan(audit.audit_id, value="UNKNOWN-TAG", actor="U1")
    assert unknown.result == "unknown"
    assert unknown.actual_location == "STORE-A"

    result = service.close(audit.audit_id, actor="U1")
    assert result.matched_asset_ids == ("A-1",)
    assert result.missing_asset_ids == ("A-2",)
    assert result.misplaced_asset_ids == ("A-3",)
    assert result.unexpected_values == ("UNKNOWN-TAG",)

    assert repo.load_asset("A-1").location_id == "STORE-A"
    assert repo.load_asset("A-2").location_id == "STORE-A"
    assert repo.load_asset("A-3").location_id == "STORE-B"


def test_issued_and_disposed_assets_are_not_expected_in_location_audit(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_locations(repo)
    seed_asset(repo, "A-1", "STORE-A", AssetLifecycle.IN_STOCK)
    seed_asset(repo, "A-2", "STORE-A", AssetLifecycle.ISSUED)
    seed_asset(repo, "A-3", "STORE-A", AssetLifecycle.DISPOSED)
    service = SerializedAssetAuditService(repo)

    audit = service.open(location_id="STORE-A", actor="U1")
    result = service.result(audit.audit_id)

    assert result.expected_count == 1
    assert result.missing_asset_ids == ("A-1",)


def test_duplicate_scan_and_second_open_audit_are_rejected(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_locations(repo)
    seed_asset(repo, "A-1", "STORE-A")
    service = SerializedAssetAuditService(repo)
    audit = service.open(location_id="STORE-A", actor="U1")

    with pytest.raises(InventoryDomainError, match="already has an open asset audit"):
        service.open(location_id="STORE-A", actor="U2")

    service.scan(audit.audit_id, value="A-1", actor="U1")
    with pytest.raises(InventoryDomainError, match="already recorded"):
        service.scan(audit.audit_id, value="A-1", actor="U1")


def test_closed_audit_rejects_further_scans(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_locations(repo)
    seed_asset(repo, "A-1", "STORE-A")
    service = SerializedAssetAuditService(repo)
    audit = service.open(location_id="STORE-A", actor="U1")
    service.close(audit.audit_id, actor="U1")

    with pytest.raises(InventoryDomainError, match="not open"):
        service.scan(audit.audit_id, value="A-1", actor="U1")

from pathlib import Path

import pytest

from src.inventory.commands import InventoryCommandService
from src.inventory.domain import AssetLifecycle, InventoryDomainError, SerializedAsset
from src.inventory.repository import InventoryRepository
from src.knowledge.file_loader import UploadValidationError, _reject_html_masquerading_as_file


def seed_locations(commands):
    commands.execute("create location SITE-A type site site SITE-A name Main Site", actor="admin")
    commands.execute(
        "create location STORE-A type storeroom site SITE-A name Main Store parent SITE-A",
        actor="admin",
    )
    commands.execute(
        "create location SHELF-A1 type shelf site SITE-A name Shelf A1 parent STORE-A",
        actor="admin",
    )
    commands.execute(
        "create location REPAIR-CAGE type cage site SITE-A name Repair Cage parent STORE-A",
        actor="admin",
    )


def seed_asset(repo, *, status=AssetLifecycle.RETURNED):
    with repo.transaction() as conn:
        repo.save_asset(
            SerializedAsset(
                asset_id="A-1",
                sku="LAPTOP-01",
                serial_number="SER-001",
                status=status,
                location_id="SHELF-A1",
                purchase_order_id="PO-1",
            ),
            conn,
        )


def test_serial_lookup_and_returned_custody_are_human_readable(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed_locations(commands)
    seed_asset(repo)

    result = commands.execute("asset serial ser-001", actor="U1")

    assert "A-1" in result
    assert "SER-001" in result
    assert "Home/storage location" in result
    assert "returned; awaiting inspection / put-away" in result


def test_returned_asset_can_be_inspected_ready_for_stock(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed_locations(commands)
    seed_asset(repo)

    result = commands.execute("inspect asset A-1 ready at SHELF-A1", actor="TECH")
    asset = repo.load_asset("A-1")

    assert "Inspection passed" in result
    assert asset is not None
    assert asset.status == AssetLifecycle.IN_STOCK
    assert asset.location_id == "SHELF-A1"


def test_returned_asset_can_be_routed_to_repair_from_inspection(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed_locations(commands)
    seed_asset(repo)

    result = commands.execute("inspect asset A-1 repair at REPAIR-CAGE note keyboard fault", actor="TECH")
    asset = repo.load_asset("A-1")

    assert "repair" in result
    assert asset is not None
    assert asset.status == AssetLifecycle.REPAIR
    assert asset.location_id == "REPAIR-CAGE"


def test_slack_backticks_around_service_id_are_accepted(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed_asset(repo, status=AssetLifecycle.REPAIR)
    opened = commands.execute("open service inspection for asset A-1 note test", actor="TECH")
    service_id = opened.split("`")[1]

    result = commands.execute(f"service `{service_id}`", actor="TECH")

    assert service_id in result
    assert "Status: *open*" in result


def test_angle_brackets_around_service_id_are_accepted(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed_asset(repo, status=AssetLifecycle.REPAIR)
    opened = commands.execute("open service inspection for asset A-1 note test", actor="TECH")
    service_id = opened.split("`")[1]

    result = commands.execute(f"service <{service_id}>", actor="TECH")

    assert service_id in result


def test_multiline_slack_commands_are_rejected(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)

    with pytest.raises(InventoryDomainError, match="one inventory command per Slack message"):
        commands.execute(
            "create supplier DELL name Dell South Africa\n"
            "create customer EMP-1 type employee name Test User",
            actor="U1",
        )


def test_slack_html_download_is_rejected_with_scope_hint(tmp_path):
    path = Path(tmp_path) / "fake.csv"
    path.write_text("<!DOCTYPE html><html><body>Slack login</body></html>", encoding="utf-8")

    with pytest.raises(UploadValidationError, match="files:read"):
        _reject_html_masquerading_as_file(path)


def test_normal_csv_is_not_rejected_as_html(tmp_path):
    path = Path(tmp_path) / "real.csv"
    path.write_text("sku,quantity\nLAPTOP-1,2\n", encoding="utf-8")

    _reject_html_masquerading_as_file(path)

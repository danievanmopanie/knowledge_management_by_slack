from datetime import datetime, timezone

from src.inventory.commands import InventoryCommandService
from src.inventory.domain import AssetLifecycle, SerializedAsset, StockTransaction
from src.inventory.repository import InventoryRepository


def seed_asset(repo):
    with repo.transaction() as conn:
        repo.save_asset(
            SerializedAsset(
                asset_id="A-1",
                sku="LAPTOP-01",
                serial_number="SER-1",
                status=AssetLifecycle.RECEIVED,
                location_id="RECV",
                purchase_order_id="PO-1",
            ),
            conn,
        )


def seed_stock(repo):
    with repo.transaction() as conn:
        repo.post_stock_transaction(
            StockTransaction(
                transaction_id="seed-stock",
                sku="MOUSE-01",
                quantity=10,
                transaction_type="receive",
                actor="seed",
                to_location="STORE-A",
            ),
            conn,
        )


def test_asset_commands_drive_persisted_lifecycle(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo)
    commands = InventoryCommandService(repo)

    response = commands.execute("store asset A-1 at SHELF-B3", actor="U1")
    assert "in_stock" in response

    response = commands.execute(
        "issue asset A-1 to U123 customer EMP-42 loan until 2026-08-20",
        actor="U1",
    )
    assert "loan until" in response

    status = commands.execute("status asset A-1", actor="U1")
    assert "issued" in status
    assert "EMP-42" in status
    assert "2026-08-20" in status

    commands.execute("return asset A-1", actor="U1")
    persisted = repo.load_asset("A-1")
    assert persisted is not None
    assert persisted.status == AssetLifecycle.RETURNED
    assert len(repo.asset_movements("A-1")) == 3


def test_stock_commands_reserve_issue_transfer_and_count(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_stock(repo)
    commands = InventoryCommandService(repo)

    status = commands.execute("stock MOUSE-01 at STORE-A", actor="U1")
    assert "On hand: *10*" in status
    assert "Available: *10*" in status

    reserved = commands.execute(
        "reserve stock MOUSE-01 4 at STORE-A for EMP-42",
        actor="U1",
    )
    reservation_id = reserved.split("`")[-2]

    commands.execute(
        f"issue stock MOUSE-01 4 from STORE-A to EMP-42 reservation {reservation_id}",
        actor="U1",
    )
    commands.execute("transfer stock MOUSE-01 2 from STORE-A to STORE-B", actor="U1")

    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 4
    assert repo.stock_on_hand("MOUSE-01", "STORE-B") == 2

    result = commands.execute("count stock MOUSE-01 at STORE-A = 3", actor="U1")
    assert "variance *-1*" in result
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 3


def test_unknown_command_is_rejected(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)

    try:
        commands.execute("please make inventory better", actor="U1")
    except Exception as exc:
        assert "Unsupported inventory command" in str(exc)
    else:
        raise AssertionError("Expected unsupported command failure")

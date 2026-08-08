from src.inventory.commands import InventoryCommandService
from src.inventory.customers import CustomerCustodyService
from src.inventory.domain import AssetLifecycle, SerializedAsset, StockTransaction
from src.inventory.repository import InventoryRepository


def seed_locations(commands):
    commands.execute("create location SITE-A type site site SITE-A name Main Site", actor="admin")
    commands.execute(
        "create location STORE-A type storeroom site SITE-A name Main Store parent SITE-A",
        actor="admin",
    )
    commands.execute(
        "create location STORE-B type storeroom site SITE-A name Secondary Store parent SITE-A",
        actor="admin",
    )
    commands.execute(
        "create location SHELF-B3 type shelf site SITE-A name Shelf B3 parent STORE-A",
        actor="admin",
    )


def seed_customer(repo, customer_id="EMP-42"):
    CustomerCustodyService(repo).create(
        customer_id=customer_id,
        name=f"Customer {customer_id}",
        customer_type="employee",
        actor="admin",
    )


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
    seed_customer(repo)
    commands = InventoryCommandService(repo)
    seed_locations(commands)

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
    assert "Shelf B3" in status

    commands.execute("return asset A-1", actor="U1")
    persisted = repo.load_asset("A-1")
    assert persisted is not None
    assert persisted.status == AssetLifecycle.RETURNED
    assert len(repo.asset_movements("A-1")) == 3


def test_stock_commands_reserve_issue_transfer_and_count(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_stock(repo)
    seed_customer(repo)
    commands = InventoryCommandService(repo)
    seed_locations(commands)

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


def test_location_commands_build_and_show_hierarchy(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed_locations(commands)

    listing = commands.execute("locations site SITE-A", actor="U1")
    assert "STORE-A" in listing
    assert "SHELF-B3" in listing

    path = commands.execute("location path SHELF-B3", actor="U1")
    assert path == "`SITE-A` → `STORE-A` → `SHELF-B3`"


def test_unknown_location_is_rejected_for_stock_and_putaway(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo)
    commands = InventoryCommandService(repo)

    try:
        commands.execute("store asset A-1 at MADE-UP-SHELF", actor="U1")
    except Exception as exc:
        assert "Unknown inventory location" in str(exc)
    else:
        raise AssertionError("Expected unknown-location failure")


def test_unknown_command_is_rejected(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)

    try:
        commands.execute("please make inventory better", actor="U1")
    except Exception as exc:
        assert "Unsupported inventory command" in str(exc)
    else:
        raise AssertionError("Expected unsupported command failure")

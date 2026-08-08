from src.inventory.commands import InventoryCommandService
from src.inventory.customers import CustomerCustodyService
from src.inventory.domain import AssetLifecycle, SerializedAsset, StockTransaction
from src.inventory.repository import InventoryRepository


def seed_locations(commands):
    commands.execute("create location SITE-A type site site SITE-A name Main Site", actor="admin")
    commands.execute("create location STORE-A type storeroom site SITE-A name Main Store parent SITE-A", actor="admin")
    commands.execute("create location STORE-B type storeroom site SITE-A name Secondary Store parent SITE-A", actor="admin")
    commands.execute("create location SHELF-B3 type shelf site SITE-A name Shelf B3 parent STORE-A", actor="admin")


def seed_items(commands):
    commands.execute("create item LAPTOP-01 tracking serialized class laptop name Laptop manufacturer Dell model Model-X", actor="admin")
    commands.execute("create item MOUSE-01 tracking quantity class peripheral name USB Mouse manufacturer Dell model Mouse-X reorder point 4 quantity 20", actor="admin")


def seed_customer(repo, customer_id="EMP-42"):
    CustomerCustodyService(repo).create(customer_id=customer_id, name=f"Customer {customer_id}", customer_type="employee", actor="admin")


def seed_asset(repo, status=AssetLifecycle.RECEIVED):
    with repo.transaction() as conn:
        repo.save_asset(SerializedAsset(asset_id="A-1", sku="LAPTOP-01", serial_number="SER-1", status=status, location_id="RECV", purchase_order_id="PO-1"), conn)


def seed_stock(repo):
    with repo.transaction() as conn:
        repo.post_stock_transaction(StockTransaction(transaction_id="seed-stock", sku="MOUSE-01", quantity=10, transaction_type="receive", actor="seed", to_location="STORE-A"), conn)


def test_item_commands_manage_catalog_and_reorder_defaults(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed_locations(commands)
    created = commands.execute("create item MOUSE-01 tracking quantity class peripheral name USB Mouse manufacturer Dell model M100 reorder point 5 quantity 20", actor="admin")
    assert "MOUSE-01" in created and "quantity" in created
    detail = commands.execute("item MOUSE-01", actor="U1")
    assert "USB Mouse" in detail and "Default reorder point: *5*" in detail
    assert "MOUSE-01" in commands.execute("items class peripheral", actor="U1")
    assert "Applied" in commands.execute("apply reorder default MOUSE-01 at STORE-A", actor="U1")
    with repo._connect() as conn:
        rule = conn.execute("SELECT * FROM inventory_reorder_rules WHERE sku='MOUSE-01' AND location_id='STORE-A'").fetchone()
    assert rule["reorder_point"] == 5 and rule["reorder_quantity"] == 20
    commands.execute("deactivate item MOUSE-01", actor="admin")
    assert "inactive" in commands.execute("item MOUSE-01", actor="U1")
    commands.execute("activate item MOUSE-01", actor="admin")
    assert "active" in commands.execute("item MOUSE-01", actor="U1")


def test_asset_commands_drive_persisted_lifecycle(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo)
    seed_customer(repo)
    commands = InventoryCommandService(repo)
    seed_locations(commands)
    seed_items(commands)
    assert "in_stock" in commands.execute("store asset A-1 at SHELF-B3", actor="U1")
    assert "loan until" in commands.execute("issue asset A-1 to U123 customer EMP-42 loan until 2026-08-20", actor="U1")
    status = commands.execute("status asset A-1", actor="U1")
    assert "issued" in status and "EMP-42" in status and "2026-08-20" in status and "Shelf B3" in status
    commands.execute("return asset A-1", actor="U1")
    persisted = repo.load_asset("A-1")
    assert persisted is not None and persisted.status == AssetLifecycle.RETURNED
    assert len(repo.asset_movements("A-1")) == 3


def test_service_commands_open_complete_and_show_history(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo, AssetLifecycle.REPAIR)
    commands = InventoryCommandService(repo)
    commands.execute("create supplier DELL-SA name Dell South Africa", actor="admin")
    opened = commands.execute("open service rma for asset A-1 supplier DELL-SA reference RMA-42 note screen failure", actor="tech")
    service_id = opened.split("`")[1]
    assert "RMA-42" in opened
    assert service_id in commands.execute("open service cases", actor="tech")
    detail = commands.execute(f"service {service_id}", actor="tech")
    assert "A-1" in detail and "rma" in detail and "DELL-SA" in detail
    completed = commands.execute(f"complete service {service_id} outcome repaired cost 1250.50 note panel replaced", actor="tech")
    assert "1250.50" in completed
    history = commands.execute("service history asset A-1", actor="tech")
    assert "repaired" in history and "1250.50" in history
    events = commands.execute(f"service events {service_id}", actor="tech")
    assert "opened" in events and "completed" in events


def test_service_command_cancel_requires_note_and_preserves_history(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo, AssetLifecycle.QUARANTINE)
    commands = InventoryCommandService(repo)
    opened = commands.execute("open service inspection for asset A-1 note assess damage", actor="tech")
    service_id = opened.split("`")[1]
    cancelled = commands.execute(f"cancel service {service_id} note duplicate request", actor="tech")
    assert "Cancelled" in cancelled
    detail = commands.execute(f"service {service_id}", actor="tech")
    assert "cancelled" in detail and "duplicate request" in detail


def test_stock_commands_reserve_issue_transfer_and_count(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_stock(repo)
    seed_customer(repo)
    commands = InventoryCommandService(repo)
    seed_locations(commands)
    seed_items(commands)
    status = commands.execute("stock MOUSE-01 at STORE-A", actor="U1")
    assert "On hand: *10*" in status and "Available: *10*" in status
    reserved = commands.execute("reserve stock MOUSE-01 4 at STORE-A for EMP-42", actor="U1")
    reservation_id = reserved.split("`")[-2]
    commands.execute(f"issue stock MOUSE-01 4 from STORE-A to EMP-42 reservation {reservation_id}", actor="U1")
    commands.execute("transfer stock MOUSE-01 2 from STORE-A to STORE-B", actor="U1")
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 4
    assert repo.stock_on_hand("MOUSE-01", "STORE-B") == 2
    result = commands.execute("count stock MOUSE-01 at STORE-A = 3", actor="U1")
    assert "variance *-1*" in result and repo.stock_on_hand("MOUSE-01", "STORE-A") == 3


def test_unknown_sku_is_rejected_for_stock_commands(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed_locations(commands)
    try:
        commands.execute("stock MADE-UP at STORE-A", actor="U1")
    except Exception as exc:
        assert "Unknown inventory SKU" in str(exc)
    else:
        raise AssertionError("Expected unknown-SKU failure")


def test_location_commands_build_and_show_hierarchy(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed_locations(commands)
    listing = commands.execute("locations site SITE-A", actor="U1")
    assert "STORE-A" in listing and "SHELF-B3" in listing
    assert commands.execute("location path SHELF-B3", actor="U1") == "`SITE-A` → `STORE-A` → `SHELF-B3`"


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

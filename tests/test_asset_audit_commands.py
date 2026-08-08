from src.inventory.commands import InventoryCommandService
from src.inventory.domain import AssetLifecycle, SerializedAsset
from src.inventory.repository import InventoryRepository


def seed(commands, repo):
    commands.execute("create location SITE-A type site site SITE-A name Main Site", actor="admin")
    commands.execute(
        "create location STORE-A type storeroom site SITE-A name Main Store parent SITE-A",
        actor="admin",
    )
    commands.execute(
        "create location STORE-B type storeroom site SITE-A name Other Store parent SITE-A",
        actor="admin",
    )
    with repo.transaction() as conn:
        for asset_id, location in (("A-1", "STORE-A"), ("A-2", "STORE-A"), ("A-3", "STORE-B")):
            repo.save_asset(
                SerializedAsset(
                    asset_id=asset_id,
                    sku="LAPTOP-01",
                    serial_number=f"SER-{asset_id}",
                    status=AssetLifecycle.IN_STOCK,
                    location_id=location,
                    purchase_order_id="PO-1",
                ),
                conn,
            )


def test_open_scan_detail_and_close_asset_audit(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed(commands, repo)

    opened = commands.execute("open asset audit STORE-A note quarterly verification", actor="U1")
    audit_id = opened.split("`")[1]
    assert "Expected assets snapshot: *2*" in opened

    assert audit_id in commands.execute("open asset audits", actor="U1")
    assert "matched" in commands.execute(f"scan asset {audit_id} A-1", actor="U1")

    misplaced = commands.execute(f"scan asset {audit_id} SER-A-3", actor="U1")
    assert "physically found at `STORE-A`" in misplaced
    assert "ledger says `STORE-B`" in misplaced
    assert repo.load_asset("A-3").location_id == "STORE-B"

    assert "not a known serialized asset" in commands.execute(
        f"scan asset {audit_id} UNKNOWN-TAG", actor="U1"
    )

    detail = commands.execute(f"asset audit {audit_id}", actor="U1")
    assert "Missing: *1*" in detail
    assert "Misplaced: *1*" in detail
    assert "Unknown scans: *1*" in detail

    scans = commands.execute(f"asset audit scans {audit_id}", actor="U1")
    assert "A-1" in scans
    assert "A-3" in scans
    assert "UNKNOWN-TAG" in scans

    closed = commands.execute(f"close asset audit {audit_id}", actor="U1")
    assert "Status: *closed*" in closed
    assert "No asset location or lifecycle state was changed automatically." in closed


def test_existing_profile_and_base_commands_still_work_with_audit_composition(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed(commands, repo)

    assert "Main Site" in commands.execute("locations site SITE-A", actor="U1")
    profile = commands.execute("asset profile A-1", actor="U1")
    assert "Asset lifecycle profile" in profile

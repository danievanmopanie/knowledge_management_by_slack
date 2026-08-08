from datetime import datetime, timezone

from src.inventory.commands import InventoryCommandService
from src.inventory.domain import AssetLifecycle, SerializedAsset
from src.inventory.lost_investigations import LostAssetInvestigationService
from src.inventory.repository import InventoryRepository


def seed(repo):
    commands = InventoryCommandService(repo)
    asset = SerializedAsset(
        asset_id="A-1",
        sku="LAPTOP-01",
        serial_number="SER-1",
        status=AssetLifecycle.IN_STOCK,
        location_id="STORE-A",
        purchase_order_id="PO-1",
        metadata={"audit_investigation_status": "lost_investigation"},
    )
    with repo.transaction() as conn:
        repo.save_asset(asset, conn)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO inventory_asset_audits(
                audit_id,location_id,status,opened_by,opened_at,closed_by,closed_at,note
            ) VALUES ('AUD-1','STORE-A','closed','u1',?,'u1',?,'test')""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO inventory_asset_audit_findings(
                finding_id,audit_id,finding_key,finding_type,asset_id,observed_value,
                expected_location,observed_location,status,created_at,resolution_type,
                resolution_note,resolved_by,resolved_at
            ) VALUES ('FND-1','AUD-1','missing:A-1','missing','A-1','','STORE-A','',
                'resolved',?,'lost_investigation','investigate','tech',?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO inventory_asset_audit_corrections(
                correction_id,finding_id,action,asset_id,from_location,to_location,
                actor,at,note
            ) VALUES ('COR-1','FND-1','lost_investigation','A-1','STORE-A','STORE-A',
                'tech',?,'security review')""",
            (now,),
        )
    return commands


def test_open_assign_note_close_found_investigation(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = seed(repo)

    opened = commands.execute("open lost investigation COR-1 owner U-SEC", actor="tech")
    investigation_id = opened.split("`")[1]
    assert "U-SEC" in opened

    commands.execute(f"assign lost investigation {investigation_id} to U-MGR", actor="tech")
    commands.execute(f"note lost investigation {investigation_id} Checked security footage", actor="U-MGR")
    closed = commands.execute(
        f"close lost investigation {investigation_id} outcome found note located in meeting room",
        actor="U-MGR",
    )
    assert "found" in closed

    service = LostAssetInvestigationService(repo)
    item = service.require(investigation_id)
    assert item.status == "closed"
    assert item.outcome == "found"
    assert item.owner == "U-MGR"
    assert [event.action for event in service.events(investigation_id)] == [
        "opened",
        "assigned",
        "note",
        "closed",
    ]
    assert "audit_investigation_status" not in repo.load_asset("A-1").metadata


def test_open_from_correction_is_idempotent(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = seed(repo)
    first = commands.execute("open lost investigation COR-1", actor="tech")
    second = commands.execute("open lost investigation COR-1", actor="tech")
    assert first.split("`")[1] == second.split("`")[1]
    assert len(LostAssetInvestigationService(repo).list(status="all")) == 1


def test_list_and_history_commands(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = seed(repo)
    opened = commands.execute("open lost investigation COR-1 owner U-SEC", actor="tech")
    investigation_id = opened.split("`")[1]
    assert investigation_id in commands.execute("lost investigations", actor="tech")
    assert "A-1" in commands.execute(f"lost investigation {investigation_id}", actor="tech")
    assert "opened" in commands.execute(
        f"lost investigation history {investigation_id}", actor="tech"
    )

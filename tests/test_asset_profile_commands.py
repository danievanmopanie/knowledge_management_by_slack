from datetime import datetime, timezone

from src.inventory.commands import InventoryCommandService
from src.inventory.domain import AssetLifecycle, SerializedAsset
from src.inventory.repository import InventoryRepository


def seed_asset(
    repo,
    *,
    asset_id="A-1",
    status=AssetLifecycle.IN_STOCK,
    received_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
    warranty_end=datetime(2024, 1, 1, tzinfo=timezone.utc),
):
    with repo.transaction() as conn:
        repo.save_asset(
            SerializedAsset(
                asset_id=asset_id,
                sku="LAPTOP-01",
                serial_number=f"SER-{asset_id}",
                status=status,
                location_id="STORE-A",
                purchase_order_id="PO-1",
                received_at=received_at,
                warranty_end=warranty_end,
            ),
            conn,
        )


def test_profile_and_warranty_age_reports_are_available_through_main_commands(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo)
    commands = InventoryCommandService(repo)

    profile = commands.execute("asset profile A-1", actor="U1")
    assert "expired" in profile
    assert "Retirement eligible: *yes*" in profile

    out = commands.execute("out of warranty", actor="U1")
    assert "A-1" in out

    old = commands.execute("assets older than 4 years", actor="U1")
    assert "A-1" in old

    candidates = commands.execute("retirement candidates", actor="U1")
    assert "A-1" in candidates


def test_lifecycle_dates_can_be_maintained_through_commands(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo, received_at=None, warranty_end=None)
    commands = InventoryCommandService(repo)

    result = commands.execute(
        "set asset dates A-1 received 2022-08-01 warranty 2025-08-01",
        actor="U1",
    )

    assert "2022-08-01" in result
    assert "2025-08-01" in result
    asset = repo.load_asset("A-1")
    assert asset.received_at.date().isoformat() == "2022-08-01"
    assert asset.warranty_end.date().isoformat() == "2025-08-01"


def test_disposal_queue_evidence_and_dispose_work_end_to_end(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo, status=AssetLifecycle.RETIRED)
    commands = InventoryCommandService(repo)

    queue = commands.execute("awaiting disposal", actor="U1")
    assert "A-1" in queue
    assert "disposal evidence: *0*" in queue

    recorded = commands.execute(
        "add disposal evidence A-1 type certificate reference CERT-42 note certified destruction",
        actor="U1",
    )
    assert "certificate" in recorded

    evidence = commands.execute("disposal evidence A-1", actor="U1")
    assert "CERT-42" in evidence

    disposed = commands.execute("dispose asset A-1", actor="U1")
    assert "Disposed" in disposed
    assert repo.load_asset("A-1").status == AssetLifecycle.DISPOSED


def test_existing_non_profile_commands_still_delegate_to_base_parser(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)

    created = commands.execute(
        "create location SITE-A type site site SITE-A name Main Site",
        actor="admin",
    )

    assert "SITE-A" in created

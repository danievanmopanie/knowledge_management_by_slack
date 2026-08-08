from datetime import datetime, timezone

from src.inventory.asset_lifecycle import SerializedAssetLifecycleService
from src.inventory.asset_profile import AssetLifecycleProfileService
from src.inventory.commands import InventoryCommandService
from src.inventory.domain import AssetLifecycle, InventoryDomainError, SerializedAsset
from src.inventory.loss_governance import AssetLossGovernanceService
from src.inventory.lost_investigations import LostAssetInvestigationService
from src.inventory.repository import InventoryRepository


def seed_confirmed_loss(repo):
    asset = SerializedAsset(
        asset_id="A-LOST",
        sku="LAPTOP-01",
        serial_number="SER-LOST",
        status=AssetLifecycle.RETIRED,
        location_id="STORE-A",
        purchase_order_id="PO-1",
    )
    with repo.transaction() as conn:
        repo.save_asset(asset, conn)
    LostAssetInvestigationService(repo)
    now = datetime.now(timezone.utc).isoformat()
    with repo.transaction() as conn:
        conn.execute(
            """INSERT INTO inventory_lost_asset_investigations(
                investigation_id,correction_id,finding_id,asset_id,owner,status,
                opened_at,opened_by,outcome,closed_at,closed_by
            ) VALUES ('INV-LOST','COR-LOST','FND-LOST','A-LOST','U-SEC','closed',
                ?,'tech','confirmed_lost',?,'U-SEC')""",
            (now, now),
        )
    return asset


def seed_location(commands):
    commands.execute("create location SITE-A type site site SITE-A name Main Site", actor="admin")
    commands.execute("create location STORE-A type storeroom site SITE-A name Main Store parent SITE-A", actor="admin")
    commands.execute("create location STORE-B type storeroom site SITE-A name Recovery Store parent SITE-A", actor="admin")


def test_loss_requires_separate_approval_and_writeoff_reference(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_confirmed_loss(repo)
    service = AssetLossGovernanceService(repo)

    loss = service.request("INV-LOST", actor="requester", note="security investigation confirmed loss")
    assert loss.status == "pending_approval"
    try:
        service.approve(loss.loss_id, actor="requester", financial_reference="FIN-1", note="approve")
    except InventoryDomainError as exc:
        assert "other than the requester" in str(exc)
    else:
        raise AssertionError("Expected separation-of-duties failure")

    approved = service.approve(loss.loss_id, actor="manager", financial_reference="FIN-1", note="approved")
    assert approved.status == "approved"
    written = service.write_off(approved.loss_id, actor="finance", writeoff_reference="WO-1", note="posted")
    assert written.status == "written_off"
    assert [event.action for event in service.events(loss.loss_id)] == ["requested", "approved", "written_off"]


def test_confirmed_lost_disposal_blocked_until_writeoff(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_confirmed_loss(repo)
    profiles = AssetLifecycleProfileService(repo)
    profiles.add_disposal_evidence(
        "A-LOST", evidence_type="certificate", reference="CERT-1", note="disposal evidence", actor="admin"
    )
    lifecycle = SerializedAssetLifecycleService(repo)
    try:
        lifecycle.dispose("A-LOST", actor="admin")
    except InventoryDomainError as exc:
        assert "financial write-off" in str(exc)
    else:
        raise AssertionError("Expected confirmed-loss write-off gate")

    losses = AssetLossGovernanceService(repo)
    loss = losses.request("INV-LOST", actor="requester", note="confirmed")
    losses.approve(loss.loss_id, actor="manager", financial_reference="FIN-1", note="approved")
    losses.write_off(loss.loss_id, actor="finance", writeoff_reference="WO-1", note="posted")
    disposed = lifecycle.dispose("A-LOST", actor="admin")
    assert disposed.status == AssetLifecycle.DISPOSED


def test_recovery_after_writeoff_requires_reference_and_updates_location_only(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_confirmed_loss(repo)
    commands = InventoryCommandService(repo)
    seed_location(commands)
    service = AssetLossGovernanceService(repo)
    loss = service.request("INV-LOST", actor="requester", note="confirmed")
    service.approve(loss.loss_id, actor="manager", financial_reference="FIN-1", note="approved")
    service.write_off(loss.loss_id, actor="finance", writeoff_reference="WO-1", note="posted")

    recovered = service.recover(
        loss.loss_id,
        actor="tech",
        location_id="STORE-B",
        recovery_reference="REV-1",
        note="asset returned by employee",
    )
    asset = repo.load_asset("A-LOST")
    assert recovered.status == "recovered"
    assert asset is not None and asset.location_id == "STORE-B"
    assert asset.status == AssetLifecycle.RETIRED
    assert service.disposal_allowed("A-LOST") is False


def test_loss_slack_commands_cover_full_financial_flow(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_confirmed_loss(repo)
    commands = InventoryCommandService(repo)
    seed_location(commands)

    requested = commands.execute("request loss INV-LOST note security confirmed loss", actor="requester")
    loss_id = requested.split("`")[1]
    assert "pending_approval" in requested
    assert "FIN-99" in commands.execute(
        f"approve loss {loss_id} financial FIN-99 note manager approved", actor="manager"
    )
    assert "WO-99" in commands.execute(
        f"write off loss {loss_id} reference WO-99 note finance posted", actor="finance"
    )
    assert loss_id in commands.execute("loss cases written_off", actor="auditor")
    assert "written_off" in commands.execute(f"loss history {loss_id}", actor="auditor")

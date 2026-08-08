from datetime import datetime, timezone

import pytest

from src.inventory.audit_corrections import AssetAuditCorrectionService
from src.inventory.commands import InventoryCommandService
from src.inventory.domain import AssetLifecycle, InventoryDomainError, SerializedAsset
from src.inventory.repository import InventoryRepository


def seed(repo):
    commands = InventoryCommandService(repo)
    commands.execute("create location SITE-A type site site SITE-A name Main Site", actor="admin")
    commands.execute("create location STORE-A type storeroom site SITE-A name Store A parent SITE-A", actor="admin")
    commands.execute("create location STORE-B type storeroom site SITE-A name Store B parent SITE-A", actor="admin")
    asset = SerializedAsset(
        asset_id="A-1",
        sku="LAPTOP-01",
        serial_number="SER-1",
        status=AssetLifecycle.IN_STOCK,
        location_id="STORE-B",
        purchase_order_id="PO-1",
    )
    with repo.transaction() as conn:
        repo.save_asset(asset, conn)
        conn.execute(
            """INSERT INTO inventory_asset_audits(
                audit_id,location_id,status,opened_by,opened_at,closed_by,closed_at,note
            ) VALUES ('AUD-1','STORE-A','closed','u1',?,'u1',?,'test')""",
            (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
    return commands


def add_finding(repo, finding_id, finding_type, *, asset_id="A-1", observed="", expected="STORE-A", observed_location="STORE-A"):
    with repo.transaction() as conn:
        conn.execute(
            """INSERT INTO inventory_asset_audit_findings(
                finding_id,audit_id,finding_key,finding_type,asset_id,observed_value,
                expected_location,observed_location,status,created_at
            ) VALUES (?,?,?,?,?,?,?,?,'open',?)""",
            (
                finding_id,
                "AUD-1",
                f"{finding_type}:{finding_id}",
                finding_type,
                asset_id,
                observed,
                expected,
                observed_location,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def test_relocation_correction_moves_location_only_and_resolves_finding(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed(repo)
    add_finding(repo, "FND-MOVE", "misplaced", observed_location="STORE-A")
    service = AssetAuditCorrectionService(repo)

    correction = service.relocate("FND-MOVE", actor="tech", note="physically verified")

    asset = repo.load_asset("A-1")
    finding = service.findings.require("FND-MOVE")
    assert correction.to_location == "STORE-A"
    assert asset.location_id == "STORE-A"
    assert asset.status == AssetLifecycle.IN_STOCK
    assert asset.metadata["last_audit_finding"] == "FND-MOVE"
    assert finding.status == "resolved"
    assert finding.resolution_type == "relocated"


def test_lost_investigation_marks_metadata_without_changing_location_or_status(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed(repo)
    add_finding(repo, "FND-MISS", "missing", observed_location="")
    service = AssetAuditCorrectionService(repo)

    correction = service.start_lost_investigation("FND-MISS", actor="tech", note="security review opened")

    asset = repo.load_asset("A-1")
    assert correction.action == "lost_investigation"
    assert asset.location_id == "STORE-B"
    assert asset.status == AssetLifecycle.IN_STOCK
    assert asset.metadata["audit_investigation_status"] == "lost_investigation"
    assert service.findings.require("FND-MISS").resolution_type == "lost_investigation"


def test_unknown_scan_can_be_dismissed_without_asset_write(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed(repo)
    add_finding(repo, "FND-UNK", "unknown", asset_id="", observed="BAD-TAG", expected="", observed_location="STORE-A")
    service = AssetAuditCorrectionService(repo)

    correction = service.dismiss_unknown_scan("FND-UNK", actor="tech", note="damaged legacy sticker")

    assert correction.asset_id == ""
    assert service.findings.require("FND-UNK").resolution_type == "false_scan"
    assert repo.load_asset("A-1").location_id == "STORE-B"


def test_correction_action_must_match_finding_type_and_only_run_once(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed(repo)
    add_finding(repo, "FND-MOVE", "misplaced", observed_location="STORE-A")
    service = AssetAuditCorrectionService(repo)

    with pytest.raises(InventoryDomainError, match="missing-asset"):
        service.start_lost_investigation("FND-MOVE", actor="tech", note="wrong action")

    service.relocate("FND-MOVE", actor="tech", note="verified")
    with pytest.raises(InventoryDomainError, match="not open"):
        service.relocate("FND-MOVE", actor="tech", note="again")


def test_slack_correction_commands_apply_and_show_audit_trail(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = seed(repo)
    add_finding(repo, "FND-MOVE", "misplaced", observed_location="STORE-A")

    result = commands.execute(
        "correct audit finding FND-MOVE relocate note physically verified",
        actor="tech",
    )
    correction_id = result.split("`")[1]
    assert "STORE-B" in result and "STORE-A" in result
    assert correction_id in commands.execute("asset corrections A-1", actor="tech")
    detail = commands.execute(f"audit correction {correction_id}", actor="tech")
    assert "FND-MOVE" in detail and "relocate" in detail

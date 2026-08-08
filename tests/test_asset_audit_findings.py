from src.inventory.audit_findings import AssetAuditFindingService
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


def close_audit_with_findings(commands):
    opened = commands.execute("open asset audit STORE-A", actor="U1")
    audit_id = opened.split("`")[1]
    commands.execute(f"scan asset {audit_id} A-1", actor="U1")
    commands.execute(f"scan asset {audit_id} A-3", actor="U1")
    commands.execute(f"scan asset {audit_id} UNKNOWN-TAG", actor="U1")
    commands.execute(f"close asset audit {audit_id}", actor="U1")
    return audit_id


def test_materialize_is_idempotent_and_creates_missing_misplaced_unknown(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed(commands, repo)
    audit_id = close_audit_with_findings(commands)
    service = AssetAuditFindingService(repo)

    first = service.materialize(audit_id, actor="U1")
    second = service.materialize(audit_id, actor="U1")

    assert len(first) == 3
    assert len(second) == 3
    assert {item.finding_type for item in first} == {"missing", "misplaced", "unknown"}
    misplaced = next(item for item in first if item.finding_type == "misplaced")
    assert misplaced.asset_id == "A-3"
    assert misplaced.expected_location == "STORE-B"
    assert misplaced.observed_location == "STORE-A"
    assert repo.load_asset("A-3").location_id == "STORE-B"


def test_slack_finding_resolution_and_reopen_are_auditable_and_non_mutating(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    seed(commands, repo)
    audit_id = close_audit_with_findings(commands)

    created = commands.execute(f"create audit findings {audit_id}", actor="U1")
    assert "3" in created
    listing = commands.execute(f"audit findings audit {audit_id}", actor="U1")
    assert "missing" in listing and "misplaced" in listing and "unknown" in listing

    findings = AssetAuditFindingService(repo).list(audit_id=audit_id)
    misplaced = next(item for item in findings if item.finding_type == "misplaced")
    resolved = commands.execute(
        f"resolve audit finding {misplaced.finding_id} as relocated note physically moved after verification",
        actor="U2",
    )
    assert "records the decision only" in resolved
    assert repo.load_asset("A-3").location_id == "STORE-B"

    detail = commands.execute(f"audit finding {misplaced.finding_id}", actor="U2")
    assert "relocated" in detail
    assert "Inventory changed by this finding: *no*" in detail

    history = commands.execute(f"audit finding history {misplaced.finding_id}", actor="U2")
    assert "created" in history and "resolved" in history

    commands.execute(
        f"reopen audit finding {misplaced.finding_id} note relocation not independently verified",
        actor="U3",
    )
    reopened = AssetAuditFindingService(repo).require(misplaced.finding_id)
    assert reopened.status == "open"

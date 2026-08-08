from src.inventory.commands import InventoryCommandService
from src.inventory.exceptions import InventoryExceptionService
from src.inventory.procurement import DiscrepancyType, ReconciliationDiscrepancy
from src.inventory.repository import InventoryRepository


def seed_exception(repo: InventoryRepository) -> int:
    discrepancy = ReconciliationDiscrepancy(
        discrepancy_type=DiscrepancyType.SHORT,
        delivery_line_id="DL-1",
        po_line_id="10",
        sku="MOUSE-01",
        expected_quantity=10,
        actual_quantity=8,
        detail="Expected 10 units; delivery contained 8.",
    )
    with repo.transaction() as conn:
        repo.save_discrepancy("PO-100", "DEL-100", discrepancy, conn)
        return int(conn.execute("SELECT MAX(exception_id) FROM inventory_exceptions").fetchone()[0])


def test_exception_can_be_resolved_and_reopened_with_history(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    exception_id = seed_exception(repo)
    service = InventoryExceptionService(repo)

    resolved = service.resolve(
        exception_id,
        resolution_type="supplier_replacement",
        note="Supplier confirmed two replacement units for next delivery.",
        actor="U1",
    )

    assert resolved.status == "resolved"
    assert resolved.resolution_type == "supplier_replacement"
    assert resolved.resolved_by == "U1"
    assert len(service.events(exception_id)) == 1

    reopened = service.reopen(
        exception_id,
        note="Replacement shipment missed agreed delivery date.",
        actor="U2",
    )

    assert reopened.status == "open"
    assert reopened.resolution_type == ""
    assert [event.action for event in service.events(exception_id)] == ["resolved", "reopened"]


def test_exception_commands_list_detail_and_resolve(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    exception_id = seed_exception(repo)
    commands = InventoryCommandService(repo)

    listing = commands.execute("exceptions po PO-100", actor="U1")
    assert f"#{exception_id}" in listing
    assert "short" in listing

    detail = commands.execute(f"exception {exception_id}", actor="U1")
    assert "MOUSE-01" in detail
    assert "Expected 10 units" in detail

    response = commands.execute(
        f"resolve exception {exception_id} as supplier_credit note Supplier issued credit note CN-42",
        actor="U1",
    )
    assert "supplier_credit" in response

    resolved = commands.execute("exceptions status resolved", actor="U1")
    assert f"#{exception_id}" in resolved

    history = commands.execute(f"exception history {exception_id}", actor="U1")
    assert "resolved" in history
    assert "CN-42" in history


def test_resolution_requires_supported_type_and_note(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    exception_id = seed_exception(repo)
    service = InventoryExceptionService(repo)

    try:
        service.resolve(
            exception_id,
            resolution_type="magic_fix",
            note="Something happened",
            actor="U1",
        )
    except Exception as exc:
        assert "Resolution type" in str(exc)
    else:
        raise AssertionError("Expected unsupported resolution type failure")

from pathlib import Path

from src.inventory.assisted_receiving import AssistedReceivingWorkflow
from src.inventory.domain import TrackingMode
from src.inventory.procurement import PurchaseOrder, PurchaseOrderLine
from src.inventory.repository import InventoryRepository


def seed_po(repo: InventoryRepository) -> None:
    po = PurchaseOrder(
        purchase_order_id="PO-100",
        supplier="Supplier A",
        created_by="U1",
        lines=[
            PurchaseOrderLine(
                line_id="10",
                sku="MOUSE-01",
                description="Mouse",
                quantity_ordered=5,
                tracking_mode=TrackingMode.QUANTITY,
            )
        ],
    )
    repo.save_purchase_order(po)


def write_delivery(path: Path, quantity: int) -> None:
    path.write_text(
        "po_line_id,sku,quantity,damaged_quantity,serial_numbers\n"
        f"10,MOUSE-01,{quantity},0,\n",
        encoding="utf-8",
    )


def test_stage_does_not_mutate_inventory_until_confirmed(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_po(repo)
    path = tmp_path / "delivery.csv"
    write_delivery(path, 5)
    workflow = AssistedReceivingWorkflow(repo)

    receipt, preview = workflow.stage(
        purchase_order_id="PO-100",
        source_path=path,
        destination_location="STORE-A",
        actor="U1",
    )

    assert "Receipt preview" in preview
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 0

    message = workflow.confirm(receipt.receipt_id, actor="U1")

    assert "confirmed" in message
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 5


def test_stage_surfaces_short_delivery_before_confirmation(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_po(repo)
    path = tmp_path / "delivery.csv"
    write_delivery(path, 3)
    workflow = AssistedReceivingWorkflow(repo)

    receipt, preview = workflow.stage(
        purchase_order_id="PO-100",
        source_path=path,
        destination_location="STORE-A",
        actor="U1",
    )

    assert "short" in preview.lower()
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 0

    workflow.confirm(receipt.receipt_id, actor="U1")
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 3
    assert repo.exception_count() >= 1


def test_cancel_leaves_inventory_unchanged(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_po(repo)
    path = tmp_path / "delivery.csv"
    write_delivery(path, 5)
    workflow = AssistedReceivingWorkflow(repo)

    receipt, _ = workflow.stage(
        purchase_order_id="PO-100",
        source_path=path,
        destination_location="STORE-A",
        actor="U1",
    )

    message = workflow.cancel(receipt.receipt_id, actor="U1")

    assert "not changed" in message
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 0

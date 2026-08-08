from pathlib import Path

import pytest

from src.inventory.domain import TrackingMode
from src.inventory.procurement import Delivery, DeliveryLine, PurchaseOrder, PurchaseOrderLine
from src.inventory.receiving_service import AtomicReceivingService
from src.inventory.repository import InventoryRepository


def quantity_po():
    return PurchaseOrder(
        purchase_order_id="PO-1",
        supplier="Supplier",
        created_by="buyer",
        lines=[
            PurchaseOrderLine(
                line_id="L1",
                sku="MOUSE-01",
                description="Mouse",
                quantity_ordered=10,
                tracking_mode=TrackingMode.QUANTITY,
            )
        ],
    )


def test_atomic_quantity_receipt_persists_po_stock_and_exception(tmp_path: Path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = AtomicReceivingService(repository=repo)
    po = quantity_po()
    delivery = Delivery(
        delivery_id="D1",
        purchase_order_id="PO-1",
        supplier_delivery_note="DN1",
        received_by="receiver",
        lines=[DeliveryLine("DL1", "MOUSE-01", 8, po_line_id="L1")],
    )

    result = service.receive(
        purchase_order=po,
        delivery=delivery,
        destination_location="STORE-A",
        actor="receiver",
    )

    assert result.has_exceptions is True
    assert po.lines[0].quantity_received == 8
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 8
    assert repo.exception_count() == 1


def test_atomic_serialized_receipt_creates_assets(tmp_path: Path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = AtomicReceivingService(repository=repo)
    po = PurchaseOrder(
        purchase_order_id="PO-LAP",
        supplier="Supplier",
        created_by="buyer",
        lines=[PurchaseOrderLine("L1", "LAP-01", "Laptop", 2, TrackingMode.SERIALIZED)],
    )
    delivery = Delivery(
        delivery_id="D-LAP",
        purchase_order_id="PO-LAP",
        supplier_delivery_note="DN-LAP",
        received_by="receiver",
        lines=[
            DeliveryLine(
                "DL1",
                "LAP-01",
                2,
                po_line_id="L1",
                serial_numbers=("SN001", "SN002"),
            )
        ],
    )

    result = service.receive(
        purchase_order=po,
        delivery=delivery,
        destination_location="STORE-A",
        actor="receiver",
    )

    assert result.has_exceptions is False
    assert repo.asset_count() == 2
    assert po.lines[0].quantity_received == 2


def test_database_failure_rolls_back_po_and_stock(tmp_path: Path):
    class FailingRepository(InventoryRepository):
        def post_stock_transaction(self, tx, conn):
            super().post_stock_transaction(tx, conn)
            raise RuntimeError("simulated failure")

    repo = FailingRepository(tmp_path / "inventory.db")
    service = AtomicReceivingService(repository=repo)
    po = quantity_po()
    delivery = Delivery(
        delivery_id="D1",
        purchase_order_id="PO-1",
        supplier_delivery_note="DN1",
        received_by="receiver",
        lines=[DeliveryLine("DL1", "MOUSE-01", 10, po_line_id="L1")],
    )

    with pytest.raises(RuntimeError, match="simulated failure"):
        service.receive(
            purchase_order=po,
            delivery=delivery,
            destination_location="STORE-A",
            actor="receiver",
        )

    assert po.lines[0].quantity_received == 0
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 0
    assert repo.asset_count() == 0

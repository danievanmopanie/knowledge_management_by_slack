from src.inventory.domain import InventoryDomainError, TrackingMode
from src.inventory.procurement import (
    Delivery,
    DeliveryLine,
    DeliveryReconciler,
    DeliveryStatus,
    DiscrepancyType,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)


def po():
    return PurchaseOrder(
        purchase_order_id="PO-1",
        supplier="Supplier A",
        created_by="tester",
        lines=[
            PurchaseOrderLine(
                line_id="1",
                sku="LAPTOP-01",
                description="Laptop",
                quantity_ordered=2,
                tracking_mode=TrackingMode.SERIALIZED,
            ),
            PurchaseOrderLine(
                line_id="2",
                sku="MOUSE-01",
                description="Mouse",
                quantity_ordered=10,
                tracking_mode=TrackingMode.QUANTITY,
            ),
        ],
    )


def test_clean_delivery_reconciles_and_accepts():
    order = po()
    delivery = Delivery(
        delivery_id="D1",
        purchase_order_id="PO-1",
        supplier_delivery_note="DN-1",
        received_by="receiver",
        lines=[
            DeliveryLine("DL1", "LAPTOP-01", 2, po_line_id="1", serial_numbers=("S1", "S2")),
            DeliveryLine("DL2", "MOUSE-01", 10, po_line_id="2"),
        ],
    )
    reconciler = DeliveryReconciler()
    result = reconciler.reconcile(order, delivery)

    assert result.has_exceptions is False
    assert result.accepted_quantities == {"1": 2, "2": 10}
    reconciler.accept(order, delivery, result)
    assert order.status == PurchaseOrderStatus.RECEIVED
    assert delivery.status == DeliveryStatus.ACCEPTED


def test_short_over_and_damaged_are_classified():
    order = po()
    delivery = Delivery(
        delivery_id="D2",
        purchase_order_id="PO-1",
        supplier_delivery_note="DN-2",
        received_by="receiver",
        lines=[
            DeliveryLine("DL1", "LAPTOP-01", 1, po_line_id="1", serial_numbers=("S1",)),
            DeliveryLine("DL2", "MOUSE-01", 12, po_line_id="2", damaged_quantity=2),
        ],
    )
    result = DeliveryReconciler().reconcile(order, delivery)
    kinds = {item.discrepancy_type for item in result.discrepancies}

    assert DiscrepancyType.SHORT in kinds
    assert DiscrepancyType.OVER in kinds
    assert DiscrepancyType.DAMAGED in kinds
    assert result.accepted_quantities["1"] == 1
    assert result.accepted_quantities["2"] == 10
    assert delivery.status == DeliveryStatus.EXCEPTION


def test_wrong_sku_and_unexpected_line_are_flagged():
    order = po()
    delivery = Delivery(
        delivery_id="D3",
        purchase_order_id="PO-1",
        supplier_delivery_note="DN-3",
        received_by="receiver",
        lines=[
            DeliveryLine("DL1", "WRONG", 2, po_line_id="1"),
            DeliveryLine("DL2", "HEADSET-01", 4),
        ],
    )
    result = DeliveryReconciler().reconcile(order, delivery)
    kinds = {item.discrepancy_type for item in result.discrepancies}

    assert DiscrepancyType.WRONG_SKU in kinds
    assert DiscrepancyType.UNEXPECTED_LINE in kinds


def test_serialized_receipt_requires_serial_per_accepted_unit():
    order = po()
    delivery = Delivery(
        delivery_id="D4",
        purchase_order_id="PO-1",
        supplier_delivery_note="DN-4",
        received_by="receiver",
        lines=[DeliveryLine("DL1", "LAPTOP-01", 2, po_line_id="1", serial_numbers=("S1",))],
    )

    try:
        DeliveryReconciler().reconcile(order, delivery)
    except InventoryDomainError as exc:
        assert "serial number" in str(exc)
    else:
        raise AssertionError("Expected serialized receipt validation error")


def test_partial_accept_updates_po_status_without_over_receipt():
    order = po()
    delivery = Delivery(
        delivery_id="D5",
        purchase_order_id="PO-1",
        supplier_delivery_note="DN-5",
        received_by="receiver",
        lines=[DeliveryLine("DL1", "MOUSE-01", 5, po_line_id="2")],
    )
    reconciler = DeliveryReconciler()
    result = reconciler.reconcile(order, delivery)
    reconciler.accept(order, delivery, result)

    assert order.line("2").quantity_received == 5
    assert order.status == PurchaseOrderStatus.PARTIALLY_RECEIVED

from src.inventory.domain import AssetLifecycle, TrackingMode
from src.inventory.procurement import (
    Delivery,
    DeliveryLine,
    DeliveryReconciler,
    PurchaseOrder,
    PurchaseOrderLine,
)
from src.inventory.receiving import ReceivingPlanner


def test_receiving_plan_creates_stock_transaction_and_assets():
    order = PurchaseOrder(
        purchase_order_id="PO-1",
        supplier="Supplier A",
        created_by="tester",
        lines=[
            PurchaseOrderLine("1", "LAPTOP-01", "Laptop", 2, TrackingMode.SERIALIZED),
            PurchaseOrderLine("2", "MOUSE-01", "Mouse", 4, TrackingMode.QUANTITY),
        ],
    )
    delivery = Delivery(
        delivery_id="D1",
        purchase_order_id="PO-1",
        supplier_delivery_note="DN-1",
        received_by="receiver",
        lines=[
            DeliveryLine("DL1", "LAPTOP-01", 2, po_line_id="1", serial_numbers=("S1", "S2")),
            DeliveryLine("DL2", "MOUSE-01", 4, po_line_id="2"),
        ],
    )
    recon = DeliveryReconciler().reconcile(order, delivery)
    plan = ReceivingPlanner().build(
        purchase_order=order,
        delivery=delivery,
        reconciliation=recon,
        destination_location="STORE-A",
        actor="receiver",
    )

    assert len(plan.stock_transactions) == 1
    assert plan.stock_transactions[0].sku == "MOUSE-01"
    assert plan.stock_transactions[0].quantity == 4
    assert len(plan.serialized_assets) == 2
    assert {asset.serial_number for asset in plan.serialized_assets} == {"S1", "S2"}
    assert all(asset.status == AssetLifecycle.RECEIVED for asset in plan.serialized_assets)

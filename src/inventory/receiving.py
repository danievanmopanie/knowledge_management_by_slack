"""Translate accepted delivery reconciliation into deterministic stock/asset receipt plans."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.inventory.domain import AssetLifecycle, SerializedAsset, StockTransaction, TrackingMode
from src.inventory.procurement import Delivery, DeliveryReconciliation, PurchaseOrder


@dataclass
class ReceivingPlan:
    stock_transactions: list[StockTransaction] = field(default_factory=list)
    serialized_assets: list[SerializedAsset] = field(default_factory=list)


class ReceivingPlanner:
    """Builds a receipt plan; persistence/commit remains a later atomic service."""

    def build(
        self,
        *,
        purchase_order: PurchaseOrder,
        delivery: Delivery,
        reconciliation: DeliveryReconciliation,
        destination_location: str,
        actor: str,
    ) -> ReceivingPlan:
        plan = ReceivingPlan()
        delivery_lines = {line.po_line_id: line for line in delivery.lines if line.po_line_id}

        for po_line_id, quantity in reconciliation.accepted_quantities.items():
            if quantity <= 0:
                continue
            po_line = purchase_order.line(po_line_id)
            delivered = delivery_lines.get(po_line_id)
            if po_line.tracking_mode == TrackingMode.QUANTITY:
                plan.stock_transactions.append(
                    StockTransaction(
                        transaction_id=f"{delivery.delivery_id}:{po_line_id}:receive",
                        sku=po_line.sku,
                        quantity=quantity,
                        transaction_type="receive",
                        actor=actor,
                        to_location=destination_location,
                        reference=delivery.supplier_delivery_note,
                    )
                )
            else:
                serials = tuple(delivered.serial_numbers[:quantity]) if delivered else ()
                for index, serial_number in enumerate(serials, 1):
                    plan.serialized_assets.append(
                        SerializedAsset(
                            asset_id=f"{delivery.delivery_id}:{po_line_id}:{index:03d}",
                            sku=po_line.sku,
                            serial_number=serial_number,
                            status=AssetLifecycle.RECEIVED,
                            location_id=destination_location,
                            purchase_order_id=purchase_order.purchase_order_id,
                        )
                    )
        return plan

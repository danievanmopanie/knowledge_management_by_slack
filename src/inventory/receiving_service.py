"""Transactional receiving service: reconcile, accept and persist one delivery atomically."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from src.inventory.procurement import Delivery, DeliveryReconciler, DeliveryReconciliation, PurchaseOrder
from src.inventory.receiving import ReceivingPlanner
from src.inventory.repository import InventoryRepository


class AtomicReceivingService:
    """Commit a physical delivery as one database transaction."""

    def __init__(
        self,
        repository: InventoryRepository | None = None,
        reconciler: DeliveryReconciler | None = None,
        planner: ReceivingPlanner | None = None,
    ) -> None:
        self.repository = repository or InventoryRepository()
        self.reconciler = reconciler or DeliveryReconciler()
        self.planner = planner or ReceivingPlanner()

    def receive(
        self,
        *,
        purchase_order: PurchaseOrder,
        delivery: Delivery,
        destination_location: str,
        actor: str,
    ) -> DeliveryReconciliation:
        """Reconcile and atomically persist PO, delivery, stock/assets and exceptions."""
        result = self.reconciler.reconcile(purchase_order, delivery)
        plan = self.planner.build(
            purchase_order=purchase_order,
            delivery=delivery,
            reconciliation=result,
            destination_location=destination_location,
            actor=actor,
        )

        # Mutate in-memory PO/delivery only immediately before entering the
        # transaction. If persistence fails, restore the original values.
        old_po_status = purchase_order.status
        old_delivery_status = delivery.status
        old_received = {line.line_id: line.quantity_received for line in purchase_order.lines}

        try:
            self.reconciler.accept(purchase_order, delivery, result)
            with self.repository.transaction() as conn:
                self.repository.save_purchase_order(purchase_order, conn)
                self.repository.save_delivery(delivery, conn)
                for tx in plan.stock_transactions:
                    self.repository.post_stock_transaction(tx, conn)
                now = datetime.now(timezone.utc)
                for asset in plan.serialized_assets:
                    if asset.received_at is None:
                        asset = replace(asset, received_at=now)
                    self.repository.save_asset(asset, conn)
                for discrepancy in result.discrepancies:
                    self.repository.save_discrepancy(
                        purchase_order.purchase_order_id,
                        delivery.delivery_id,
                        discrepancy,
                        conn,
                    )
        except Exception:
            purchase_order.status = old_po_status
            delivery.status = old_delivery_status
            for line in purchase_order.lines:
                line.quantity_received = old_received[line.line_id]
            raise

        return result

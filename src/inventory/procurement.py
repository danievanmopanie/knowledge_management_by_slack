"""Purchase order, delivery receipt and reconciliation domain logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from src.inventory.domain import InventoryDomainError, TrackingMode


class PurchaseOrderStatus(str, Enum):
    OPEN = "open"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class DeliveryStatus(str, Enum):
    RECEIVED = "received"
    RECONCILED = "reconciled"
    ACCEPTED = "accepted"
    EXCEPTION = "exception"


class DiscrepancyType(str, Enum):
    SHORT = "short"
    OVER = "over"
    WRONG_SKU = "wrong_sku"
    DAMAGED = "damaged"
    UNEXPECTED_LINE = "unexpected_line"


@dataclass
class PurchaseOrderLine:
    line_id: str
    sku: str
    description: str
    quantity_ordered: int
    tracking_mode: TrackingMode
    unit_price: float = 0.0
    quantity_received: int = 0
    model: str = ""

    @property
    def quantity_outstanding(self) -> int:
        return max(0, self.quantity_ordered - self.quantity_received)


@dataclass
class PurchaseOrder:
    purchase_order_id: str
    supplier: str
    lines: list[PurchaseOrderLine]
    created_by: str
    status: PurchaseOrderStatus = PurchaseOrderStatus.OPEN
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    external_reference: str = ""

    def line(self, line_id: str) -> PurchaseOrderLine:
        for item in self.lines:
            if item.line_id == line_id:
                return item
        raise InventoryDomainError(f"Unknown PO line: {line_id}")

    def refresh_status(self) -> None:
        if self.status in {PurchaseOrderStatus.CLOSED, PurchaseOrderStatus.CANCELLED}:
            return
        total_ordered = sum(line.quantity_ordered for line in self.lines)
        total_received = sum(line.quantity_received for line in self.lines)
        if total_received <= 0:
            self.status = PurchaseOrderStatus.OPEN
        elif total_received < total_ordered:
            self.status = PurchaseOrderStatus.PARTIALLY_RECEIVED
        else:
            self.status = PurchaseOrderStatus.RECEIVED


@dataclass(frozen=True)
class DeliveryLine:
    delivery_line_id: str
    sku: str
    quantity_delivered: int
    po_line_id: str = ""
    damaged_quantity: int = 0
    serial_numbers: tuple[str, ...] = ()
    note: str = ""


@dataclass
class Delivery:
    delivery_id: str
    purchase_order_id: str
    supplier_delivery_note: str
    lines: list[DeliveryLine]
    received_by: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: DeliveryStatus = DeliveryStatus.RECEIVED


@dataclass(frozen=True)
class ReconciliationDiscrepancy:
    discrepancy_type: DiscrepancyType
    delivery_line_id: str
    po_line_id: str = ""
    sku: str = ""
    expected_quantity: int = 0
    actual_quantity: int = 0
    damaged_quantity: int = 0
    detail: str = ""


@dataclass
class DeliveryReconciliation:
    purchase_order_id: str
    delivery_id: str
    accepted_quantities: dict[str, int] = field(default_factory=dict)
    discrepancies: list[ReconciliationDiscrepancy] = field(default_factory=list)

    @property
    def has_exceptions(self) -> bool:
        return bool(self.discrepancies)


class DeliveryReconciler:
    """Matches a physical delivery to a PO without mutating stock."""

    def reconcile(self, purchase_order: PurchaseOrder, delivery: Delivery) -> DeliveryReconciliation:
        if delivery.purchase_order_id != purchase_order.purchase_order_id:
            raise InventoryDomainError("Delivery does not belong to this purchase order.")
        if purchase_order.status in {PurchaseOrderStatus.CLOSED, PurchaseOrderStatus.CANCELLED}:
            raise InventoryDomainError("Cannot receive against a closed or cancelled purchase order.")

        result = DeliveryReconciliation(
            purchase_order_id=purchase_order.purchase_order_id,
            delivery_id=delivery.delivery_id,
        )
        delivery_by_po_line: dict[str, int] = {}

        for delivered in delivery.lines:
            if delivered.quantity_delivered <= 0:
                raise InventoryDomainError("Delivered quantity must be positive.")
            if delivered.damaged_quantity < 0 or delivered.damaged_quantity > delivered.quantity_delivered:
                raise InventoryDomainError("Damaged quantity must be between zero and delivered quantity.")

            po_line = self._match_line(purchase_order, delivered)
            if po_line is None:
                result.discrepancies.append(
                    ReconciliationDiscrepancy(
                        discrepancy_type=DiscrepancyType.UNEXPECTED_LINE,
                        delivery_line_id=delivered.delivery_line_id,
                        sku=delivered.sku,
                        actual_quantity=delivered.quantity_delivered,
                        detail="Delivery line could not be matched to the purchase order.",
                    )
                )
                continue

            if delivered.sku != po_line.sku:
                result.discrepancies.append(
                    ReconciliationDiscrepancy(
                        discrepancy_type=DiscrepancyType.WRONG_SKU,
                        delivery_line_id=delivered.delivery_line_id,
                        po_line_id=po_line.line_id,
                        sku=delivered.sku,
                        expected_quantity=po_line.quantity_outstanding,
                        actual_quantity=delivered.quantity_delivered,
                        detail=f"Expected SKU {po_line.sku}, received {delivered.sku}.",
                    )
                )
                continue

            if po_line.tracking_mode == TrackingMode.SERIALIZED:
                usable = delivered.quantity_delivered - delivered.damaged_quantity
                if len(delivered.serial_numbers) != usable:
                    raise InventoryDomainError(
                        f"Serialized PO line {po_line.line_id} requires one serial number per accepted unit."
                    )

            delivered_to_line = delivery_by_po_line.get(po_line.line_id, 0) + delivered.quantity_delivered
            delivery_by_po_line[po_line.line_id] = delivered_to_line

            outstanding = po_line.quantity_outstanding
            accepted = min(max(delivered.quantity_delivered - delivered.damaged_quantity, 0), outstanding)
            result.accepted_quantities[po_line.line_id] = (
                result.accepted_quantities.get(po_line.line_id, 0) + accepted
            )

            if delivered.damaged_quantity:
                result.discrepancies.append(
                    ReconciliationDiscrepancy(
                        discrepancy_type=DiscrepancyType.DAMAGED,
                        delivery_line_id=delivered.delivery_line_id,
                        po_line_id=po_line.line_id,
                        sku=po_line.sku,
                        expected_quantity=outstanding,
                        actual_quantity=delivered.quantity_delivered,
                        damaged_quantity=delivered.damaged_quantity,
                        detail=f"{delivered.damaged_quantity} delivered unit(s) marked damaged.",
                    )
                )

            if delivered.quantity_delivered > outstanding:
                result.discrepancies.append(
                    ReconciliationDiscrepancy(
                        discrepancy_type=DiscrepancyType.OVER,
                        delivery_line_id=delivered.delivery_line_id,
                        po_line_id=po_line.line_id,
                        sku=po_line.sku,
                        expected_quantity=outstanding,
                        actual_quantity=delivered.quantity_delivered,
                        detail=f"Delivered {delivered.quantity_delivered}, outstanding quantity is {outstanding}.",
                    )
                )

        for po_line in purchase_order.lines:
            delivered_qty = delivery_by_po_line.get(po_line.line_id, 0)
            outstanding = po_line.quantity_outstanding
            if delivered_qty < outstanding:
                result.discrepancies.append(
                    ReconciliationDiscrepancy(
                        discrepancy_type=DiscrepancyType.SHORT,
                        delivery_line_id="",
                        po_line_id=po_line.line_id,
                        sku=po_line.sku,
                        expected_quantity=outstanding,
                        actual_quantity=delivered_qty,
                        detail=f"Outstanding {outstanding}; this delivery contains {delivered_qty}.",
                    )
                )

        delivery.status = DeliveryStatus.EXCEPTION if result.has_exceptions else DeliveryStatus.RECONCILED
        return result

    def accept(self, purchase_order: PurchaseOrder, delivery: Delivery, result: DeliveryReconciliation) -> None:
        """Apply only reconciled accepted quantities to the PO; stock receipt is a later service."""
        if result.purchase_order_id != purchase_order.purchase_order_id or result.delivery_id != delivery.delivery_id:
            raise InventoryDomainError("Reconciliation does not match the PO/delivery pair.")
        for line_id, quantity in result.accepted_quantities.items():
            line = purchase_order.line(line_id)
            if line.quantity_received + quantity > line.quantity_ordered:
                raise InventoryDomainError("Accepted delivery would over-receive the purchase order line.")
            line.quantity_received += quantity
        purchase_order.refresh_status()
        delivery.status = DeliveryStatus.EXCEPTION if result.has_exceptions else DeliveryStatus.ACCEPTED

    @staticmethod
    def _match_line(purchase_order: PurchaseOrder, delivered: DeliveryLine) -> PurchaseOrderLine | None:
        if delivered.po_line_id:
            try:
                return purchase_order.line(delivered.po_line_id)
            except InventoryDomainError:
                return None
        matches = [line for line in purchase_order.lines if line.sku == delivered.sku]
        return matches[0] if len(matches) == 1 else None

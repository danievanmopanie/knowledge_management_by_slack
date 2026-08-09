"""Core inventory domain: catalog, stock, serialized assets, locations and lifecycle events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TrackingMode(str, Enum):
    SERIALIZED = "serialized"
    QUANTITY = "quantity"


class AssetLifecycle(str, Enum):
    ORDERED = "ordered"
    RECEIVED = "received"
    IN_STOCK = "in_stock"
    ISSUED = "issued"
    RETURNED = "returned"
    REPAIR = "repair"
    QUARANTINE = "quarantine"
    LOST = "lost"
    RETIRED = "retired"
    DISPOSED = "disposed"


class AllocationType(str, Enum):
    DEDICATED = "dedicated"
    LOAN = "loan"


@dataclass(frozen=True)
class CatalogItem:
    sku: str
    name: str
    category: str
    tracking_mode: TrackingMode
    manufacturer: str = ""
    model: str = ""
    reorder_point: int = 0
    reorder_quantity: int = 0


@dataclass(frozen=True)
class StorageLocation:
    location_id: str
    name: str
    site: str
    parent_id: str = ""
    location_type: str = "storeroom"  # site | storeroom | cage | shelf | bin


@dataclass
class StockBalance:
    sku: str
    location_id: str
    on_hand: int = 0
    reserved: int = 0

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved


@dataclass
class SerializedAsset:
    asset_id: str
    sku: str
    serial_number: str
    status: AssetLifecycle = AssetLifecycle.ORDERED
    location_id: str = ""
    assigned_to: str = ""
    customer_ref: str = ""
    allocation_type: AllocationType | None = None
    return_due_at: datetime | None = None
    purchase_order_id: str = ""
    received_at: datetime | None = None
    warranty_end: datetime | None = None
    retired_at: datetime | None = None
    disposed_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetMovement:
    movement_id: str
    asset_id: str
    from_status: AssetLifecycle
    to_status: AssetLifecycle
    actor: str
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    from_location: str = ""
    to_location: str = ""
    customer_ref: str = ""
    note: str = ""


@dataclass(frozen=True)
class StockTransaction:
    transaction_id: str
    sku: str
    quantity: int
    transaction_type: str  # receive | issue | return | transfer | adjustment | dispose
    actor: str
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    from_location: str = ""
    to_location: str = ""
    asset_id: str = ""
    customer_ref: str = ""
    reference: str = ""
    note: str = ""


@dataclass(frozen=True)
class ReconciliationCount:
    sku: str
    location_id: str
    counted_quantity: int
    expected_quantity: int

    @property
    def variance(self) -> int:
        return self.counted_quantity - self.expected_quantity


class InventoryDomainError(ValueError):
    """Raised when an inventory business rule is violated."""


class StockLedger:
    """Deterministic quantity-stock ledger with non-negative stock enforcement."""

    def __init__(self) -> None:
        self._balances: dict[tuple[str, str], StockBalance] = {}
        self.transactions: list[StockTransaction] = []

    def balance(self, sku: str, location_id: str) -> StockBalance:
        key = (sku, location_id)
        if key not in self._balances:
            self._balances[key] = StockBalance(sku=sku, location_id=location_id)
        return self._balances[key]

    def apply(self, transaction: StockTransaction) -> None:
        if transaction.quantity <= 0:
            raise InventoryDomainError("Transaction quantity must be positive.")
        if transaction.transaction_type in {"receive", "return"}:
            if not transaction.to_location:
                raise InventoryDomainError("Destination location is required.")
            self.balance(transaction.sku, transaction.to_location).on_hand += transaction.quantity
        elif transaction.transaction_type in {"issue", "dispose"}:
            self._decrease(transaction.sku, transaction.from_location, transaction.quantity)
        elif transaction.transaction_type == "transfer":
            if not transaction.from_location or not transaction.to_location:
                raise InventoryDomainError("Transfer requires source and destination locations.")
            self._decrease(transaction.sku, transaction.from_location, transaction.quantity)
            self.balance(transaction.sku, transaction.to_location).on_hand += transaction.quantity
        elif transaction.transaction_type == "adjustment":
            raise InventoryDomainError("Use reconcile() for stock adjustments.")
        else:
            raise InventoryDomainError(f"Unsupported transaction type: {transaction.transaction_type}")
        self.transactions.append(transaction)

    def reconcile(self, count: ReconciliationCount) -> int:
        balance = self.balance(count.sku, count.location_id)
        if balance.on_hand != count.expected_quantity:
            raise InventoryDomainError("Expected quantity does not match the current ledger balance.")
        balance.on_hand = count.counted_quantity
        return count.variance

    def below_reorder_point(self, item: CatalogItem, location_id: str) -> bool:
        return self.balance(item.sku, location_id).available <= item.reorder_point

    def _decrease(self, sku: str, location_id: str, quantity: int) -> None:
        if not location_id:
            raise InventoryDomainError("Source location is required.")
        balance = self.balance(sku, location_id)
        if balance.available < quantity:
            raise InventoryDomainError(
                f"Insufficient available stock for {sku} at {location_id}: "
                f"requested {quantity}, available {balance.available}."
            )
        balance.on_hand -= quantity

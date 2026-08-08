"""Transactional operational management for non-serialized quantity stock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from src.inventory.customers import CustomerCustodyService
from src.inventory.domain import InventoryDomainError
from src.inventory.repository import InventoryRepository


@dataclass(frozen=True)
class StockReservation:
    reservation_id: str
    sku: str
    location_id: str
    quantity: int
    customer_ref: str
    requested_by: str
    status: str = "active"
    created_at: datetime = datetime.now(timezone.utc)


@dataclass(frozen=True)
class StockCountResult:
    sku: str
    location_id: str
    expected: int
    counted: int

    @property
    def variance(self) -> int:
        return self.counted - self.expected


class QuantityStockService:
    """Persisted quantity-stock operations using the shared inventory database."""

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self.customers = CustomerCustodyService(repository)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:  # repository-owned database boundary
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_stock_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    sku TEXT NOT NULL,
                    location_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    customer_ref TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inventory_stock_counts (
                    count_id TEXT PRIMARY KEY,
                    sku TEXT NOT NULL,
                    location_id TEXT NOT NULL,
                    expected_quantity INTEGER NOT NULL,
                    counted_quantity INTEGER NOT NULL,
                    variance INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    counted_at TEXT NOT NULL,
                    note TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inventory_reorder_rules (
                    sku TEXT NOT NULL,
                    location_id TEXT NOT NULL,
                    reorder_point INTEGER NOT NULL,
                    reorder_quantity INTEGER NOT NULL,
                    PRIMARY KEY (sku, location_id)
                );
                """
            )

    def available(self, sku: str, location_id: str) -> int:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT on_hand, reserved FROM inventory_stock_balances WHERE sku=? AND location_id=?",
                (sku, location_id),
            ).fetchone()
            return 0 if row is None else int(row["on_hand"]) - int(row["reserved"])

    def reserve(
        self,
        *,
        sku: str,
        location_id: str,
        quantity: int,
        customer_ref: str,
        requested_by: str,
    ) -> StockReservation:
        if quantity <= 0:
            raise InventoryDomainError("Reservation quantity must be positive.")
        customer = self.customers.require_active(customer_ref)
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT on_hand, reserved FROM inventory_stock_balances WHERE sku=? AND location_id=?",
                (sku, location_id),
            ).fetchone()
            available = 0 if row is None else int(row["on_hand"]) - int(row["reserved"])
            if available < quantity:
                raise InventoryDomainError(
                    f"Insufficient available stock for {sku} at {location_id}: requested {quantity}, available {available}."
                )
            reservation = StockReservation(
                reservation_id=str(uuid4()),
                sku=sku,
                location_id=location_id,
                quantity=quantity,
                customer_ref=customer.customer_id,
                requested_by=requested_by,
                created_at=datetime.now(timezone.utc),
            )
            conn.execute(
                "INSERT INTO inventory_stock_reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    reservation.reservation_id,
                    sku,
                    location_id,
                    quantity,
                    reservation.customer_ref,
                    requested_by,
                    reservation.status,
                    reservation.created_at.isoformat(),
                ),
            )
            conn.execute(
                "UPDATE inventory_stock_balances SET reserved=reserved+? WHERE sku=? AND location_id=?",
                (quantity, sku, location_id),
            )
            return reservation

    def release_reservation(self, reservation_id: str) -> None:
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_stock_reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if row is None or row["status"] != "active":
                raise InventoryDomainError("Unknown or inactive stock reservation.")
            conn.execute(
                "UPDATE inventory_stock_balances SET reserved=reserved-? WHERE sku=? AND location_id=?",
                (row["quantity"], row["sku"], row["location_id"]),
            )
            conn.execute(
                "UPDATE inventory_stock_reservations SET status='released' WHERE reservation_id=?",
                (reservation_id,),
            )

    def issue(
        self,
        *,
        sku: str,
        location_id: str,
        quantity: int,
        customer_ref: str,
        actor: str,
        reservation_id: str = "",
        note: str = "",
    ) -> str:
        if quantity <= 0:
            raise InventoryDomainError("Issue quantity must be positive.")
        customer = self.customers.require_active(customer_ref)
        with self.repository.transaction() as conn:
            reserved_release = 0
            if reservation_id:
                reservation = conn.execute(
                    "SELECT * FROM inventory_stock_reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                if reservation is None or reservation["status"] != "active":
                    raise InventoryDomainError("Unknown or inactive stock reservation.")
                if reservation["sku"] != sku or reservation["location_id"] != location_id:
                    raise InventoryDomainError("Reservation does not match the requested stock/location.")
                if reservation["customer_ref"] != customer.customer_id:
                    raise InventoryDomainError("Reservation belongs to a different customer.")
                if int(reservation["quantity"]) != quantity:
                    raise InventoryDomainError("Issue quantity must match the reservation quantity.")
                reserved_release = quantity

            row = conn.execute(
                "SELECT on_hand, reserved FROM inventory_stock_balances WHERE sku=? AND location_id=?",
                (sku, location_id),
            ).fetchone()
            available = 0 if row is None else int(row["on_hand"]) - int(row["reserved"]) + reserved_release
            if available < quantity:
                raise InventoryDomainError("Insufficient available stock to issue.")

            transaction_id = str(uuid4())
            conn.execute(
                "UPDATE inventory_stock_balances SET on_hand=on_hand-?, reserved=reserved-? WHERE sku=? AND location_id=?",
                (quantity, reserved_release, sku, location_id),
            )
            conn.execute(
                "INSERT INTO inventory_stock_transactions VALUES (?, ?, ?, 'issue', ?, ?, ?, '', '', ?, '', ?)",
                (
                    transaction_id,
                    sku,
                    quantity,
                    actor,
                    datetime.now(timezone.utc).isoformat(),
                    location_id,
                    customer.customer_id,
                    note,
                ),
            )
            if reservation_id:
                conn.execute(
                    "UPDATE inventory_stock_reservations SET status='fulfilled' WHERE reservation_id=?",
                    (reservation_id,),
                )
            return transaction_id

    def return_stock(
        self,
        *,
        sku: str,
        location_id: str,
        quantity: int,
        customer_ref: str,
        actor: str,
        note: str = "",
    ) -> str:
        customer = self.customers.get(customer_ref)
        if customer is None:
            raise InventoryDomainError(f"Unknown inventory customer: {customer_ref}")
        return self._increase(
            sku=sku,
            location_id=location_id,
            quantity=quantity,
            transaction_type="return",
            customer_ref=customer.customer_id,
            actor=actor,
            note=note,
        )

    def transfer(
        self,
        *,
        sku: str,
        from_location: str,
        to_location: str,
        quantity: int,
        actor: str,
        note: str = "",
    ) -> str:
        if from_location == to_location:
            raise InventoryDomainError("Transfer locations must differ.")
        if quantity <= 0:
            raise InventoryDomainError("Transfer quantity must be positive.")
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT on_hand, reserved FROM inventory_stock_balances WHERE sku=? AND location_id=?",
                (sku, from_location),
            ).fetchone()
            available = 0 if row is None else int(row["on_hand"]) - int(row["reserved"])
            if available < quantity:
                raise InventoryDomainError("Insufficient available stock to transfer.")
            conn.execute(
                "UPDATE inventory_stock_balances SET on_hand=on_hand-? WHERE sku=? AND location_id=?",
                (quantity, sku, from_location),
            )
            conn.execute(
                "INSERT INTO inventory_stock_balances(sku, location_id, on_hand, reserved) VALUES (?, ?, ?, 0) "
                "ON CONFLICT(sku, location_id) DO UPDATE SET on_hand=on_hand+excluded.on_hand",
                (sku, to_location, quantity),
            )
            transaction_id = str(uuid4())
            conn.execute(
                "INSERT INTO inventory_stock_transactions VALUES (?, ?, ?, 'transfer', ?, ?, ?, ?, '', '', '', ?)",
                (
                    transaction_id,
                    sku,
                    quantity,
                    actor,
                    datetime.now(timezone.utc).isoformat(),
                    from_location,
                    to_location,
                    note,
                ),
            )
            return transaction_id

    def count_and_reconcile(
        self,
        *,
        sku: str,
        location_id: str,
        counted_quantity: int,
        actor: str,
        note: str = "",
    ) -> StockCountResult:
        if counted_quantity < 0:
            raise InventoryDomainError("Counted quantity cannot be negative.")
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT on_hand FROM inventory_stock_balances WHERE sku=? AND location_id=?",
                (sku, location_id),
            ).fetchone()
            expected = int(row["on_hand"]) if row else 0
            result = StockCountResult(sku, location_id, expected, counted_quantity)
            count_id = str(uuid4())
            conn.execute(
                "INSERT INTO inventory_stock_counts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    count_id,
                    sku,
                    location_id,
                    expected,
                    counted_quantity,
                    result.variance,
                    actor,
                    datetime.now(timezone.utc).isoformat(),
                    note,
                ),
            )
            conn.execute(
                "INSERT INTO inventory_stock_balances(sku, location_id, on_hand, reserved) VALUES (?, ?, ?, 0) "
                "ON CONFLICT(sku, location_id) DO UPDATE SET on_hand=excluded.on_hand",
                (sku, location_id, counted_quantity),
            )
            tx_id = str(uuid4())
            conn.execute(
                "INSERT INTO inventory_stock_transactions VALUES (?, ?, ?, 'adjustment', ?, ?, ?, ?, '', '', ?, ?)",
                (
                    tx_id,
                    sku,
                    abs(result.variance),
                    actor,
                    datetime.now(timezone.utc).isoformat(),
                    location_id,
                    location_id,
                    count_id,
                    f"Reconciliation variance {result.variance}. {note}".strip(),
                ),
            )
            return result

    def set_reorder_rule(self, *, sku: str, location_id: str, reorder_point: int, reorder_quantity: int) -> None:
        if reorder_point < 0 or reorder_quantity <= 0:
            raise InventoryDomainError("Invalid reorder rule.")
        with self.repository.transaction() as conn:
            conn.execute(
                "INSERT INTO inventory_reorder_rules VALUES (?, ?, ?, ?) "
                "ON CONFLICT(sku, location_id) DO UPDATE SET reorder_point=excluded.reorder_point, "
                "reorder_quantity=excluded.reorder_quantity",
                (sku, location_id, reorder_point, reorder_quantity),
            )

    def low_stock(self) -> list[dict[str, int | str]]:
        with self.repository._connect() as conn:
            rows = conn.execute(
                """SELECT r.sku, r.location_id, r.reorder_point, r.reorder_quantity,
                COALESCE(b.on_hand, 0) AS on_hand, COALESCE(b.reserved, 0) AS reserved
                FROM inventory_reorder_rules r
                LEFT JOIN inventory_stock_balances b ON b.sku=r.sku AND b.location_id=r.location_id
                WHERE COALESCE(b.on_hand, 0)-COALESCE(b.reserved, 0) <= r.reorder_point
                ORDER BY r.sku, r.location_id"""
            ).fetchall()
            return [dict(row) for row in rows]

    def _increase(
        self,
        *,
        sku: str,
        location_id: str,
        quantity: int,
        transaction_type: str,
        customer_ref: str,
        actor: str,
        note: str,
    ) -> str:
        if quantity <= 0:
            raise InventoryDomainError("Quantity must be positive.")
        with self.repository.transaction() as conn:
            conn.execute(
                "INSERT INTO inventory_stock_balances(sku, location_id, on_hand, reserved) VALUES (?, ?, ?, 0) "
                "ON CONFLICT(sku, location_id) DO UPDATE SET on_hand=on_hand+excluded.on_hand",
                (sku, location_id, quantity),
            )
            transaction_id = str(uuid4())
            conn.execute(
                "INSERT INTO inventory_stock_transactions VALUES (?, ?, ?, ?, ?, ?, '', ?, '', ?, '', ?)",
                (
                    transaction_id,
                    sku,
                    quantity,
                    transaction_type,
                    actor,
                    datetime.now(timezone.utc).isoformat(),
                    location_id,
                    customer_ref,
                    note,
                ),
            )
            return transaction_id

"""Two-step physical stock counting and reconciliation workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from src.inventory.domain import InventoryDomainError
from src.inventory.repository import InventoryRepository


@dataclass(frozen=True)
class StagedStockCount:
    count_id: str
    sku: str
    location_id: str
    expected_on_hand: int
    expected_reserved: int
    counted_quantity: int
    actor: str
    status: str
    created_at: datetime
    note: str = ""

    @property
    def variance(self) -> int:
        return self.counted_quantity - self.expected_on_hand


class StockReconciliationWorkflow:
    """Stage a physical count and require confirmation before ledger adjustment."""

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_staged_stock_counts (
                    count_id TEXT PRIMARY KEY,
                    sku TEXT NOT NULL,
                    location_id TEXT NOT NULL,
                    expected_on_hand INTEGER NOT NULL,
                    expected_reserved INTEGER NOT NULL,
                    counted_quantity INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    confirmed_at TEXT,
                    cancelled_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_inventory_staged_counts_status
                    ON inventory_staged_stock_counts(status, location_id, sku);
                """
            )

    def stage(
        self,
        *,
        sku: str,
        location_id: str,
        counted_quantity: int,
        actor: str,
        note: str = "",
    ) -> StagedStockCount:
        if counted_quantity < 0:
            raise InventoryDomainError("Counted quantity cannot be negative.")
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT on_hand,reserved FROM inventory_stock_balances WHERE sku=? AND location_id=?",
                (sku, location_id),
            ).fetchone()
            expected_on_hand = int(row["on_hand"]) if row else 0
            expected_reserved = int(row["reserved"]) if row else 0
            pending = conn.execute(
                """SELECT count_id FROM inventory_staged_stock_counts
                WHERE sku=? AND location_id=? AND status='pending_confirmation'""",
                (sku, location_id),
            ).fetchone()
            if pending:
                raise InventoryDomainError(
                    f"A count is already awaiting confirmation for {sku} at {location_id}: {pending['count_id']}"
                )
            staged = StagedStockCount(
                count_id=f"CNT-{uuid4().hex[:10]}",
                sku=sku,
                location_id=location_id,
                expected_on_hand=expected_on_hand,
                expected_reserved=expected_reserved,
                counted_quantity=counted_quantity,
                actor=actor,
                status="pending_confirmation",
                created_at=datetime.now(timezone.utc),
                note=note.strip(),
            )
            conn.execute(
                """INSERT INTO inventory_staged_stock_counts(
                    count_id,sku,location_id,expected_on_hand,expected_reserved,
                    counted_quantity,actor,status,created_at,note
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    staged.count_id,
                    staged.sku,
                    staged.location_id,
                    staged.expected_on_hand,
                    staged.expected_reserved,
                    staged.counted_quantity,
                    staged.actor,
                    staged.status,
                    staged.created_at.isoformat(),
                    staged.note,
                ),
            )
            return staged

    def get(self, count_id: str) -> StagedStockCount | None:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_staged_stock_counts WHERE count_id=?",
                (count_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def list_pending(self, *, location_id: str = "") -> list[StagedStockCount]:
        if location_id:
            sql = (
                "SELECT * FROM inventory_staged_stock_counts "
                "WHERE status='pending_confirmation' AND location_id=? ORDER BY created_at"
            )
            params = (location_id,)
        else:
            sql = (
                "SELECT * FROM inventory_staged_stock_counts "
                "WHERE status='pending_confirmation' ORDER BY created_at"
            )
            params = ()
        with self.repository._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._from_row(row) for row in rows]

    def confirm(self, count_id: str, *, actor: str) -> StagedStockCount:
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_staged_stock_counts WHERE count_id=?",
                (count_id,),
            ).fetchone()
            if row is None:
                raise InventoryDomainError("Unknown staged stock count.")
            if row["status"] != "pending_confirmation":
                raise InventoryDomainError("Stock count is no longer awaiting confirmation.")
            if row["actor"] != actor:
                raise InventoryDomainError("Only the user who staged this count may confirm it.")

            current = conn.execute(
                "SELECT on_hand,reserved FROM inventory_stock_balances WHERE sku=? AND location_id=?",
                (row["sku"], row["location_id"]),
            ).fetchone()
            current_on_hand = int(current["on_hand"]) if current else 0
            current_reserved = int(current["reserved"]) if current else 0
            if current_on_hand != int(row["expected_on_hand"]) or current_reserved != int(
                row["expected_reserved"]
            ):
                raise InventoryDomainError(
                    "Stock changed after this count was staged. Cancel it and perform a fresh physical count."
                )
            counted = int(row["counted_quantity"])
            if counted < current_reserved:
                raise InventoryDomainError(
                    "Counted stock is below the currently reserved quantity. Resolve or release reservations before confirming the adjustment."
                )

            variance = counted - current_on_hand
            conn.execute(
                """INSERT INTO inventory_stock_counts(
                    count_id,sku,location_id,expected_quantity,counted_quantity,
                    variance,actor,counted_at,note
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    count_id,
                    row["sku"],
                    row["location_id"],
                    current_on_hand,
                    counted,
                    variance,
                    actor,
                    now.isoformat(),
                    row["note"],
                ),
            )
            conn.execute(
                """INSERT INTO inventory_stock_balances(sku,location_id,on_hand,reserved)
                VALUES (?,?,?,0)
                ON CONFLICT(sku,location_id) DO UPDATE SET on_hand=excluded.on_hand""",
                (row["sku"], row["location_id"], counted),
            )
            if variance:
                tx_id = str(uuid4())
                conn.execute(
                    """INSERT INTO inventory_stock_transactions
                    VALUES (?, ?, ?, 'adjustment', ?, ?, ?, ?, '', '', ?, ?)""",
                    (
                        tx_id,
                        row["sku"],
                        abs(variance),
                        actor,
                        now.isoformat(),
                        row["location_id"],
                        row["location_id"],
                        count_id,
                        f"Confirmed physical-count variance {variance}. {row['note']}".strip(),
                    ),
                )
            conn.execute(
                """UPDATE inventory_staged_stock_counts
                SET status='confirmed', confirmed_at=? WHERE count_id=?""",
                (now.isoformat(), count_id),
            )

        confirmed = self.get(count_id)
        if confirmed is None:
            raise RuntimeError("Confirmed stock count disappeared from persistence.")
        return confirmed

    def cancel(self, count_id: str, *, actor: str) -> StagedStockCount:
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_staged_stock_counts WHERE count_id=?",
                (count_id,),
            ).fetchone()
            if row is None or row["status"] != "pending_confirmation":
                raise InventoryDomainError("Unknown or inactive staged stock count.")
            if row["actor"] != actor:
                raise InventoryDomainError("Only the user who staged this count may cancel it.")
            conn.execute(
                """UPDATE inventory_staged_stock_counts
                SET status='cancelled', cancelled_at=? WHERE count_id=?""",
                (now.isoformat(), count_id),
            )
        cancelled = self.get(count_id)
        if cancelled is None:
            raise RuntimeError("Cancelled stock count disappeared from persistence.")
        return cancelled

    @staticmethod
    def _from_row(row) -> StagedStockCount:
        return StagedStockCount(
            count_id=row["count_id"],
            sku=row["sku"],
            location_id=row["location_id"],
            expected_on_hand=int(row["expected_on_hand"]),
            expected_reserved=int(row["expected_reserved"]),
            counted_quantity=int(row["counted_quantity"]),
            actor=row["actor"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            note=row["note"],
        )

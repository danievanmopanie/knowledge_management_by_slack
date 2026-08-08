"""Operational inventory summary and exception views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.inventory.repository import InventoryRepository


@dataclass(frozen=True)
class InventoryOverview:
    active_locations: int
    open_purchase_orders: int
    serialized_assets: int
    issued_assets: int
    overdue_loans: int
    due_soon_loans: int
    stock_units_on_hand: int
    stock_units_reserved: int
    low_stock_positions: int
    open_exceptions: int
    pending_receipts: int
    pending_purchase_orders: int
    pending_stock_counts: int


@dataclass(frozen=True)
class LoanAlert:
    asset_id: str
    sku: str
    assigned_to: str
    customer_ref: str
    due_at: datetime
    days_overdue: int


class InventoryOverviewService:
    """Read-only operational view across the persisted inventory domain."""

    def __init__(self, repository: InventoryRepository):
        self.repository = repository

    def snapshot(self, *, now: datetime | None = None) -> InventoryOverview:
        now = now or datetime.now(timezone.utc)
        due_soon = now + timedelta(days=7)
        with self.repository._connect() as conn:
            return InventoryOverview(
                active_locations=self._count(conn, "inventory_locations", "active=1"),
                open_purchase_orders=self._count(
                    conn,
                    "inventory_purchase_orders",
                    "status IN ('open','partially_received')",
                ),
                serialized_assets=self._count(
                    conn,
                    "inventory_assets",
                    "status<>'disposed'",
                ),
                issued_assets=self._count(conn, "inventory_assets", "status='issued'"),
                overdue_loans=self._count(
                    conn,
                    "inventory_assets",
                    "status='issued' AND allocation_type='loan' AND return_due_at IS NOT NULL AND return_due_at<?",
                    (now.isoformat(),),
                ),
                due_soon_loans=self._count(
                    conn,
                    "inventory_assets",
                    "status='issued' AND allocation_type='loan' AND return_due_at IS NOT NULL AND return_due_at>=? AND return_due_at<=?",
                    (now.isoformat(), due_soon.isoformat()),
                ),
                stock_units_on_hand=self._sum(conn, "inventory_stock_balances", "on_hand"),
                stock_units_reserved=self._sum(conn, "inventory_stock_balances", "reserved"),
                low_stock_positions=self._low_stock_count(conn),
                open_exceptions=self._count(conn, "inventory_exceptions", "status='open'"),
                pending_receipts=self._count_if_table(
                    conn,
                    "inventory_staged_receipts",
                    "status='pending_confirmation'",
                ),
                pending_purchase_orders=self._count_if_table(
                    conn,
                    "inventory_staged_purchase_orders",
                    "status='pending_confirmation'",
                ),
                pending_stock_counts=self._count_if_table(
                    conn,
                    "inventory_staged_stock_counts",
                    "status='pending_confirmation'",
                ),
            )

    def overdue_loans(self, *, now: datetime | None = None, limit: int = 50) -> list[LoanAlert]:
        now = now or datetime.now(timezone.utc)
        with self.repository._connect() as conn:
            rows = conn.execute(
                """SELECT asset_id,sku,assigned_to,customer_ref,return_due_at
                FROM inventory_assets
                WHERE status='issued' AND allocation_type='loan'
                  AND return_due_at IS NOT NULL AND return_due_at<?
                ORDER BY return_due_at ASC LIMIT ?""",
                (now.isoformat(), max(1, min(limit, 200))),
            ).fetchall()
        alerts: list[LoanAlert] = []
        for row in rows:
            due = datetime.fromisoformat(row["return_due_at"])
            alerts.append(
                LoanAlert(
                    asset_id=row["asset_id"],
                    sku=row["sku"],
                    assigned_to=row["assigned_to"],
                    customer_ref=row["customer_ref"],
                    due_at=due,
                    days_overdue=max(0, (now.date() - due.date()).days),
                )
            )
        return alerts

    @staticmethod
    def _table_exists(conn, table: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None

    def _count_if_table(self, conn, table: str, where: str) -> int:
        if not self._table_exists(conn, table):
            return 0
        return self._count(conn, table, where)

    def _count(self, conn, table: str, where: str = "", params: tuple = ()) -> int:
        if not self._table_exists(conn, table):
            return 0
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += " WHERE " + where
        return int(conn.execute(sql, params).fetchone()[0])

    def _sum(self, conn, table: str, column: str) -> int:
        if not self._table_exists(conn, table):
            return 0
        return int(conn.execute(f"SELECT COALESCE(SUM({column}),0) FROM {table}").fetchone()[0])

    def _low_stock_count(self, conn) -> int:
        if not self._table_exists(conn, "inventory_reorder_rules"):
            return 0
        return int(
            conn.execute(
                """SELECT COUNT(*)
                FROM inventory_reorder_rules r
                LEFT JOIN inventory_stock_balances b
                  ON b.sku=r.sku AND b.location_id=r.location_id
                WHERE COALESCE(b.on_hand,0)-COALESCE(b.reserved,0) <= r.reorder_point"""
            ).fetchone()[0]
        )

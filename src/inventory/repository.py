"""SQLite persistence for the inventory domain."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from src.core.config import settings
from src.inventory.domain import (
    AllocationType,
    AssetLifecycle,
    AssetMovement,
    SerializedAsset,
    StockTransaction,
)
from src.inventory.procurement import Delivery, PurchaseOrder, ReconciliationDiscrepancy


class InventoryRepository:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.platform_db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_purchase_orders (
                    purchase_order_id TEXT PRIMARY KEY,
                    supplier TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    external_reference TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS inventory_po_lines (
                    line_id TEXT PRIMARY KEY,
                    purchase_order_id TEXT NOT NULL,
                    sku TEXT NOT NULL,
                    description TEXT NOT NULL,
                    quantity_ordered INTEGER NOT NULL,
                    quantity_received INTEGER NOT NULL,
                    tracking_mode TEXT NOT NULL,
                    unit_price REAL NOT NULL DEFAULT 0,
                    model TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (purchase_order_id)
                        REFERENCES inventory_purchase_orders(purchase_order_id)
                );
                CREATE TABLE IF NOT EXISTS inventory_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    purchase_order_id TEXT NOT NULL,
                    supplier_delivery_note TEXT NOT NULL,
                    received_by TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inventory_delivery_lines (
                    delivery_line_id TEXT PRIMARY KEY,
                    delivery_id TEXT NOT NULL,
                    po_line_id TEXT NOT NULL DEFAULT '',
                    sku TEXT NOT NULL,
                    quantity_delivered INTEGER NOT NULL,
                    damaged_quantity INTEGER NOT NULL,
                    serial_numbers_json TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS inventory_stock_balances (
                    sku TEXT NOT NULL,
                    location_id TEXT NOT NULL,
                    on_hand INTEGER NOT NULL DEFAULT 0,
                    reserved INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (sku, location_id)
                );
                CREATE TABLE IF NOT EXISTS inventory_stock_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    transaction_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    at TEXT NOT NULL,
                    from_location TEXT NOT NULL,
                    to_location TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    customer_ref TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    note TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inventory_assets (
                    asset_id TEXT PRIMARY KEY,
                    sku TEXT NOT NULL,
                    serial_number TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    location_id TEXT NOT NULL,
                    assigned_to TEXT NOT NULL,
                    customer_ref TEXT NOT NULL,
                    purchase_order_id TEXT NOT NULL,
                    received_at TEXT,
                    warranty_end TEXT,
                    retired_at TEXT,
                    disposed_at TEXT,
                    metadata_json TEXT NOT NULL,
                    allocation_type TEXT,
                    return_due_at TEXT
                );
                CREATE TABLE IF NOT EXISTS inventory_asset_movements (
                    movement_id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL,
                    from_status TEXT NOT NULL,
                    to_status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    at TEXT NOT NULL,
                    from_location TEXT NOT NULL,
                    to_location TEXT NOT NULL,
                    customer_ref TEXT NOT NULL,
                    note TEXT NOT NULL,
                    FOREIGN KEY (asset_id) REFERENCES inventory_assets(asset_id)
                );
                CREATE TABLE IF NOT EXISTS inventory_exceptions (
                    exception_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    delivery_id TEXT NOT NULL,
                    purchase_order_id TEXT NOT NULL,
                    discrepancy_type TEXT NOT NULL,
                    delivery_line_id TEXT NOT NULL,
                    po_line_id TEXT NOT NULL,
                    sku TEXT NOT NULL,
                    expected_quantity INTEGER NOT NULL,
                    actual_quantity INTEGER NOT NULL,
                    damaged_quantity INTEGER NOT NULL,
                    detail TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open'
                );
                """
            )
            self._ensure_column(conn, "inventory_assets", "allocation_type", "TEXT")
            self._ensure_column(conn, "inventory_assets", "return_due_at", "TEXT")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_purchase_order(
        self,
        po: PurchaseOrder,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        owns = conn is None
        conn = conn or self._connect()
        try:
            conn.execute(
                """INSERT INTO inventory_purchase_orders VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(purchase_order_id) DO UPDATE SET
                supplier=excluded.supplier,
                created_by=excluded.created_by,
                status=excluded.status,
                created_at=excluded.created_at,
                external_reference=excluded.external_reference""",
                (
                    po.purchase_order_id,
                    po.supplier,
                    po.created_by,
                    po.status.value,
                    po.created_at.isoformat(),
                    po.external_reference,
                ),
            )
            for line in po.lines:
                conn.execute(
                    """INSERT INTO inventory_po_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(line_id) DO UPDATE SET
                    quantity_received=excluded.quantity_received""",
                    (
                        line.line_id,
                        po.purchase_order_id,
                        line.sku,
                        line.description,
                        line.quantity_ordered,
                        line.quantity_received,
                        line.tracking_mode.value,
                        line.unit_price,
                        line.model,
                    ),
                )
            if owns:
                conn.commit()
        finally:
            if owns:
                conn.close()

    def save_delivery(self, delivery: Delivery, conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO inventory_deliveries VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(delivery_id) DO UPDATE SET status=excluded.status""",
            (
                delivery.delivery_id,
                delivery.purchase_order_id,
                delivery.supplier_delivery_note,
                delivery.received_by,
                delivery.received_at.isoformat(),
                delivery.status.value,
            ),
        )
        for line in delivery.lines:
            conn.execute(
                """INSERT OR REPLACE INTO inventory_delivery_lines
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    line.delivery_line_id,
                    delivery.delivery_id,
                    line.po_line_id,
                    line.sku,
                    line.quantity_delivered,
                    line.damaged_quantity,
                    json.dumps(line.serial_numbers),
                    line.note,
                ),
            )

    def post_stock_transaction(
        self,
        tx: StockTransaction,
        conn: sqlite3.Connection,
    ) -> None:
        if tx.transaction_type != "receive":
            raise ValueError("Atomic receiving currently supports receive transactions only.")
        conn.execute(
            """INSERT INTO inventory_stock_balances(sku, location_id, on_hand, reserved)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(sku, location_id) DO UPDATE SET
            on_hand=on_hand+excluded.on_hand""",
            (tx.sku, tx.to_location, tx.quantity),
        )
        conn.execute(
            """INSERT INTO inventory_stock_transactions
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tx.transaction_id,
                tx.sku,
                tx.quantity,
                tx.transaction_type,
                tx.actor,
                tx.at.isoformat(),
                tx.from_location,
                tx.to_location,
                tx.asset_id,
                tx.customer_ref,
                tx.reference,
                tx.note,
            ),
        )

    def save_asset(self, asset: SerializedAsset, conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO inventory_assets(
                asset_id, sku, serial_number, status, location_id, assigned_to,
                customer_ref, purchase_order_id, received_at, warranty_end,
                retired_at, disposed_at, metadata_json, allocation_type, return_due_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                status=excluded.status,
                location_id=excluded.location_id,
                assigned_to=excluded.assigned_to,
                customer_ref=excluded.customer_ref,
                allocation_type=excluded.allocation_type,
                return_due_at=excluded.return_due_at,
                retired_at=excluded.retired_at,
                disposed_at=excluded.disposed_at,
                metadata_json=excluded.metadata_json""",
            (
                asset.asset_id,
                asset.sku,
                asset.serial_number,
                asset.status.value,
                asset.location_id,
                asset.assigned_to,
                asset.customer_ref,
                asset.purchase_order_id,
                asset.received_at.isoformat() if asset.received_at else None,
                asset.warranty_end.isoformat() if asset.warranty_end else None,
                asset.retired_at.isoformat() if asset.retired_at else None,
                asset.disposed_at.isoformat() if asset.disposed_at else None,
                json.dumps(asset.metadata),
                asset.allocation_type.value if asset.allocation_type else None,
                asset.return_due_at.isoformat() if asset.return_due_at else None,
            ),
        )

    def load_asset(self, asset_id: str, conn: sqlite3.Connection | None = None) -> SerializedAsset | None:
        owns = conn is None
        conn = conn or self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM inventory_assets WHERE asset_id=?",
                (asset_id,),
            ).fetchone()
            if not row:
                return None
            return SerializedAsset(
                asset_id=row["asset_id"],
                sku=row["sku"],
                serial_number=row["serial_number"],
                status=AssetLifecycle(row["status"]),
                location_id=row["location_id"],
                assigned_to=row["assigned_to"],
                customer_ref=row["customer_ref"],
                allocation_type=AllocationType(row["allocation_type"]) if row["allocation_type"] else None,
                return_due_at=datetime.fromisoformat(row["return_due_at"]) if row["return_due_at"] else None,
                purchase_order_id=row["purchase_order_id"],
                received_at=datetime.fromisoformat(row["received_at"]) if row["received_at"] else None,
                warranty_end=datetime.fromisoformat(row["warranty_end"]) if row["warranty_end"] else None,
                retired_at=datetime.fromisoformat(row["retired_at"]) if row["retired_at"] else None,
                disposed_at=datetime.fromisoformat(row["disposed_at"]) if row["disposed_at"] else None,
                metadata=json.loads(row["metadata_json"] or "{}"),
            )
        finally:
            if owns:
                conn.close()

    def save_asset_movement(self, movement: AssetMovement, conn: sqlite3.Connection) -> None:
        conn.execute(
            """INSERT INTO inventory_asset_movements
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                movement.movement_id,
                movement.asset_id,
                movement.from_status.value,
                movement.to_status.value,
                movement.actor,
                movement.at.isoformat(),
                movement.from_location,
                movement.to_location,
                movement.customer_ref,
                movement.note,
            ),
        )

    def asset_movements(self, asset_id: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM inventory_asset_movements WHERE asset_id=? ORDER BY at, movement_id",
                    (asset_id,),
                ).fetchall()
            )

    def save_discrepancy(
        self,
        po_id: str,
        delivery_id: str,
        discrepancy: ReconciliationDiscrepancy,
        conn: sqlite3.Connection,
    ) -> None:
        conn.execute(
            """INSERT INTO inventory_exceptions(
            delivery_id,purchase_order_id,discrepancy_type,delivery_line_id,
            po_line_id,sku,expected_quantity,actual_quantity,damaged_quantity,detail)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                delivery_id,
                po_id,
                discrepancy.discrepancy_type.value,
                discrepancy.delivery_line_id,
                discrepancy.po_line_id,
                discrepancy.sku,
                discrepancy.expected_quantity,
                discrepancy.actual_quantity,
                discrepancy.damaged_quantity,
                discrepancy.detail,
            ),
        )

    def stock_on_hand(self, sku: str, location_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT on_hand FROM inventory_stock_balances WHERE sku=? AND location_id=?",
                (sku, location_id),
            ).fetchone()
            return int(row["on_hand"]) if row else 0

    def asset_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM inventory_assets").fetchone()[0])

    def exception_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM inventory_exceptions").fetchone()[0])

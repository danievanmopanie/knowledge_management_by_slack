"""Operational review and resolution of inventory reconciliation exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.inventory.domain import InventoryDomainError
from src.inventory.repository import InventoryRepository


RESOLUTION_TYPES = {
    "supplier_replacement",
    "supplier_credit",
    "accepted_variance",
    "returned_to_supplier",
    "document_corrected",
    "investigated_no_action",
    "other",
}


@dataclass(frozen=True)
class InventoryException:
    exception_id: int
    delivery_id: str
    purchase_order_id: str
    discrepancy_type: str
    delivery_line_id: str
    po_line_id: str
    sku: str
    expected_quantity: int
    actual_quantity: int
    damaged_quantity: int
    detail: str
    status: str
    resolution_type: str = ""
    resolution_note: str = ""
    resolved_by: str = ""
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class ExceptionEvent:
    event_id: int
    exception_id: int
    action: str
    actor: str
    note: str
    at: datetime


class InventoryExceptionService:
    """Manage the lifecycle of delivery/reconciliation exceptions."""

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            self.repository._ensure_column(conn, "inventory_exceptions", "resolution_type", "TEXT NOT NULL DEFAULT ''")
            self.repository._ensure_column(conn, "inventory_exceptions", "resolution_note", "TEXT NOT NULL DEFAULT ''")
            self.repository._ensure_column(conn, "inventory_exceptions", "resolved_by", "TEXT NOT NULL DEFAULT ''")
            self.repository._ensure_column(conn, "inventory_exceptions", "resolved_at", "TEXT")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_exception_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exception_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    at TEXT NOT NULL,
                    FOREIGN KEY (exception_id) REFERENCES inventory_exceptions(exception_id)
                );
                CREATE INDEX IF NOT EXISTS idx_inventory_exception_events_exception
                    ON inventory_exception_events(exception_id, event_id);
                CREATE INDEX IF NOT EXISTS idx_inventory_exceptions_status_po
                    ON inventory_exceptions(status, purchase_order_id);
                """
            )

    def get(self, exception_id: int) -> InventoryException | None:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_exceptions WHERE exception_id=?",
                (exception_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def require(self, exception_id: int) -> InventoryException:
        item = self.get(exception_id)
        if item is None:
            raise InventoryDomainError(f"Unknown inventory exception: {exception_id}")
        return item

    def list(
        self,
        *,
        purchase_order_id: str = "",
        status: str = "open",
        limit: int = 50,
    ) -> list[InventoryException]:
        if status not in {"open", "resolved", "all"}:
            raise InventoryDomainError("Exception status must be open, resolved or all.")
        clauses: list[str] = []
        params: list[object] = []
        if status != "all":
            clauses.append("status=?")
            params.append(status)
        if purchase_order_id:
            clauses.append("purchase_order_id=?")
            params.append(purchase_order_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 200)))
        with self.repository._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_exceptions"
                + where
                + " ORDER BY exception_id DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def resolve(
        self,
        exception_id: int,
        *,
        resolution_type: str,
        note: str,
        actor: str,
    ) -> InventoryException:
        resolution_type = resolution_type.strip().lower()
        if resolution_type not in RESOLUTION_TYPES:
            raise InventoryDomainError(
                "Resolution type must be one of: " + ", ".join(sorted(RESOLUTION_TYPES)) + "."
            )
        if not note.strip():
            raise InventoryDomainError("A resolution note is required.")
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_exceptions WHERE exception_id=?",
                (exception_id,),
            ).fetchone()
            if row is None:
                raise InventoryDomainError(f"Unknown inventory exception: {exception_id}")
            if row["status"] == "resolved":
                raise InventoryDomainError("Inventory exception is already resolved.")
            conn.execute(
                """UPDATE inventory_exceptions
                SET status='resolved', resolution_type=?, resolution_note=?,
                    resolved_by=?, resolved_at=?
                WHERE exception_id=?""",
                (resolution_type, note.strip(), actor, now.isoformat(), exception_id),
            )
            conn.execute(
                """INSERT INTO inventory_exception_events(exception_id,action,actor,note,at)
                VALUES (?, 'resolved', ?, ?, ?)""",
                (exception_id, actor, f"{resolution_type}: {note.strip()}", now.isoformat()),
            )
        return self.require(exception_id)

    def reopen(self, exception_id: int, *, note: str, actor: str) -> InventoryException:
        if not note.strip():
            raise InventoryDomainError("A reopen reason is required.")
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_exceptions WHERE exception_id=?",
                (exception_id,),
            ).fetchone()
            if row is None:
                raise InventoryDomainError(f"Unknown inventory exception: {exception_id}")
            if row["status"] != "resolved":
                raise InventoryDomainError("Only a resolved exception can be reopened.")
            conn.execute(
                """UPDATE inventory_exceptions
                SET status='open', resolution_type='', resolution_note='',
                    resolved_by='', resolved_at=NULL
                WHERE exception_id=?""",
                (exception_id,),
            )
            conn.execute(
                """INSERT INTO inventory_exception_events(exception_id,action,actor,note,at)
                VALUES (?, 'reopened', ?, ?, ?)""",
                (exception_id, actor, note.strip(), now.isoformat()),
            )
        return self.require(exception_id)

    def events(self, exception_id: int) -> list[ExceptionEvent]:
        self.require(exception_id)
        with self.repository._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_exception_events WHERE exception_id=? ORDER BY event_id",
                (exception_id,),
            ).fetchall()
        return [
            ExceptionEvent(
                event_id=int(row["event_id"]),
                exception_id=int(row["exception_id"]),
                action=row["action"],
                actor=row["actor"],
                note=row["note"],
                at=datetime.fromisoformat(row["at"]),
            )
            for row in rows
        ]

    @staticmethod
    def _from_row(row) -> InventoryException:
        return InventoryException(
            exception_id=int(row["exception_id"]),
            delivery_id=row["delivery_id"],
            purchase_order_id=row["purchase_order_id"],
            discrepancy_type=row["discrepancy_type"],
            delivery_line_id=row["delivery_line_id"],
            po_line_id=row["po_line_id"],
            sku=row["sku"],
            expected_quantity=int(row["expected_quantity"]),
            actual_quantity=int(row["actual_quantity"]),
            damaged_quantity=int(row["damaged_quantity"]),
            detail=row["detail"],
            status=row["status"],
            resolution_type=row["resolution_type"] or "",
            resolution_note=row["resolution_note"] or "",
            resolved_by=row["resolved_by"] or "",
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        )

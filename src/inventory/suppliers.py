"""Governed supplier master data for inventory procurement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.inventory.domain import InventoryDomainError
from src.inventory.repository import InventoryRepository


@dataclass(frozen=True)
class InventorySupplier:
    supplier_id: str
    name: str
    contact_name: str
    email: str
    phone: str
    active: bool
    created_by: str
    created_at: datetime


class SupplierService:
    """Persist supplier master data and guard procurement against inactive vendors."""

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_suppliers (
                    supplier_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    contact_name TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_inventory_suppliers_active
                    ON inventory_suppliers(active, name, supplier_id);
                """
            )

    def create(
        self,
        *,
        supplier_id: str,
        name: str,
        actor: str,
        contact_name: str = "",
        email: str = "",
        phone: str = "",
    ) -> InventorySupplier:
        supplier_id = supplier_id.strip().upper()
        if not supplier_id or not name.strip():
            raise InventoryDomainError("Supplier ID and name are required.")
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            exists = conn.execute(
                "SELECT supplier_id FROM inventory_suppliers WHERE supplier_id=?",
                (supplier_id,),
            ).fetchone()
            if exists:
                raise InventoryDomainError(f"Inventory supplier already exists: {supplier_id}")
            conn.execute(
                """INSERT INTO inventory_suppliers(
                    supplier_id,name,contact_name,email,phone,active,created_by,created_at
                ) VALUES (?,?,?,?,?,1,?,?)""",
                (
                    supplier_id,
                    name.strip(),
                    contact_name.strip(),
                    email.strip(),
                    phone.strip(),
                    actor,
                    now.isoformat(),
                ),
            )
        supplier = self.get(supplier_id)
        if supplier is None:
            raise RuntimeError("Supplier disappeared after creation.")
        return supplier

    def get(self, supplier_id: str) -> InventorySupplier | None:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_suppliers WHERE supplier_id=?",
                (supplier_id.strip().upper(),),
            ).fetchone()
        return self._from_row(row) if row else None

    def require_active(self, supplier_id: str) -> InventorySupplier:
        supplier = self.get(supplier_id)
        if supplier is None:
            raise InventoryDomainError(f"Unknown inventory supplier: {supplier_id}")
        if not supplier.active:
            raise InventoryDomainError(f"Inventory supplier is inactive: {supplier.supplier_id}")
        return supplier

    def list(self, *, include_inactive: bool = False) -> list[InventorySupplier]:
        sql = "SELECT * FROM inventory_suppliers"
        if not include_inactive:
            sql += " WHERE active=1"
        sql += " ORDER BY name, supplier_id"
        with self.repository._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._from_row(row) for row in rows]

    def set_active(self, supplier_id: str, *, active: bool) -> InventorySupplier:
        supplier = self.get(supplier_id)
        if supplier is None:
            raise InventoryDomainError(f"Unknown inventory supplier: {supplier_id}")
        if not active:
            with self.repository._connect() as conn:
                open_pos = int(
                    conn.execute(
                        """SELECT COUNT(*) FROM inventory_purchase_orders
                        WHERE supplier=? AND status IN ('open','partially_received')""",
                        (supplier.supplier_id,),
                    ).fetchone()[0]
                )
            if open_pos:
                raise InventoryDomainError(
                    f"Cannot deactivate {supplier.supplier_id}; {open_pos} open/partially received PO(s) remain."
                )
        with self.repository.transaction() as conn:
            conn.execute(
                "UPDATE inventory_suppliers SET active=? WHERE supplier_id=?",
                (1 if active else 0, supplier.supplier_id),
            )
        updated = self.get(supplier.supplier_id)
        if updated is None:
            raise RuntimeError("Supplier disappeared after status update.")
        return updated

    @staticmethod
    def _from_row(row) -> InventorySupplier:
        return InventorySupplier(
            supplier_id=row["supplier_id"],
            name=row["name"],
            contact_name=row["contact_name"],
            email=row["email"],
            phone=row["phone"],
            active=bool(row["active"]),
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

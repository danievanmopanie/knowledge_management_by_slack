"""Governed customers and serialized-asset custody views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.inventory.domain import InventoryDomainError
from src.inventory.repository import InventoryRepository


@dataclass(frozen=True)
class InventoryCustomer:
    customer_id: str
    name: str
    customer_type: str
    site: str
    email: str
    active: bool
    created_by: str
    created_at: datetime


class CustomerCustodyService:
    """Persist customers and expose their current and historical asset custody."""

    ALLOWED_TYPES = {"employee", "contractor", "department", "external"}

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_customers (
                    customer_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    customer_type TEXT NOT NULL,
                    site TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_inventory_customers_active
                    ON inventory_customers(active, name);
                """
            )

    def create(
        self,
        *,
        customer_id: str,
        name: str,
        customer_type: str,
        actor: str,
        site: str = "",
        email: str = "",
    ) -> InventoryCustomer:
        customer_id = customer_id.strip().upper()
        customer_type = customer_type.strip().lower()
        if not customer_id or not name.strip():
            raise InventoryDomainError("Customer ID and name are required.")
        if customer_type not in self.ALLOWED_TYPES:
            allowed = ", ".join(sorted(self.ALLOWED_TYPES))
            raise InventoryDomainError(f"Customer type must be one of: {allowed}.")
        now = datetime.now().astimezone()
        with self.repository.transaction() as conn:
            exists = conn.execute(
                "SELECT customer_id FROM inventory_customers WHERE customer_id=?",
                (customer_id,),
            ).fetchone()
            if exists:
                raise InventoryDomainError(f"Customer already exists: {customer_id}")
            conn.execute(
                """INSERT INTO inventory_customers(
                    customer_id,name,customer_type,site,email,active,created_by,created_at
                ) VALUES (?,?,?,?,?,1,?,?)""",
                (customer_id, name.strip(), customer_type, site.strip(), email.strip(), actor, now.isoformat()),
            )
        customer = self.get(customer_id)
        if customer is None:
            raise RuntimeError("Customer disappeared after creation.")
        return customer

    def get(self, customer_id: str) -> InventoryCustomer | None:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_customers WHERE customer_id=?",
                (customer_id.strip().upper(),),
            ).fetchone()
        return self._from_row(row) if row else None

    def require_active(self, customer_id: str) -> InventoryCustomer:
        customer = self.get(customer_id)
        if customer is None:
            raise InventoryDomainError(f"Unknown inventory customer: {customer_id}")
        if not customer.active:
            raise InventoryDomainError(f"Inventory customer is inactive: {customer.customer_id}")
        return customer

    def list(self, *, include_inactive: bool = False) -> list[InventoryCustomer]:
        sql = "SELECT * FROM inventory_customers"
        if not include_inactive:
            sql += " WHERE active=1"
        sql += " ORDER BY name, customer_id"
        with self.repository._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._from_row(row) for row in rows]

    def set_active(self, customer_id: str, *, active: bool) -> InventoryCustomer:
        customer = self.get(customer_id)
        if customer is None:
            raise InventoryDomainError(f"Unknown inventory customer: {customer_id}")
        if not active:
            with self.repository._connect() as conn:
                issued = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM inventory_assets WHERE customer_ref=? AND status='issued'",
                        (customer.customer_id,),
                    ).fetchone()[0]
                )
            if issued:
                raise InventoryDomainError(
                    f"Cannot deactivate {customer.customer_id}; {issued} serialized asset(s) are still issued to this customer."
                )
        with self.repository.transaction() as conn:
            conn.execute(
                "UPDATE inventory_customers SET active=? WHERE customer_id=?",
                (1 if active else 0, customer.customer_id),
            )
        updated = self.get(customer.customer_id)
        if updated is None:
            raise RuntimeError("Customer disappeared after status update.")
        return updated

    def current_assets(self, customer_id: str) -> list[dict[str, object]]:
        customer = self.get(customer_id)
        if customer is None:
            raise InventoryDomainError(f"Unknown inventory customer: {customer_id}")
        with self.repository._connect() as conn:
            rows = conn.execute(
                """SELECT asset_id,sku,serial_number,assigned_to,allocation_type,return_due_at,status
                FROM inventory_assets
                WHERE customer_ref=? AND status='issued'
                ORDER BY return_due_at IS NULL, return_due_at, asset_id""",
                (customer.customer_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def custody_history(self, customer_id: str) -> list[dict[str, object]]:
        customer = self.get(customer_id)
        if customer is None:
            raise InventoryDomainError(f"Unknown inventory customer: {customer_id}")
        with self.repository._connect() as conn:
            rows = conn.execute(
                """SELECT m.movement_id,m.asset_id,m.from_status,m.to_status,m.actor,m.at,
                m.from_location,m.to_location,m.customer_ref,m.note,a.sku,a.serial_number
                FROM inventory_asset_movements m
                LEFT JOIN inventory_assets a ON a.asset_id=m.asset_id
                WHERE m.customer_ref=?
                ORDER BY m.at DESC, m.movement_id DESC""",
                (customer.customer_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def overdue_assets(self, customer_id: str = "") -> list[dict[str, object]]:
        now = datetime.now().astimezone().isoformat()
        sql = """SELECT asset_id,sku,serial_number,assigned_to,customer_ref,return_due_at
            FROM inventory_assets
            WHERE status='issued' AND allocation_type='loan'
            AND return_due_at IS NOT NULL AND return_due_at < ?"""
        params: list[object] = [now]
        if customer_id:
            sql += " AND customer_ref=?"
            params.append(customer_id.strip().upper())
        sql += " ORDER BY return_due_at, asset_id"
        with self.repository._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _from_row(row) -> InventoryCustomer:
        return InventoryCustomer(
            customer_id=row["customer_id"],
            name=row["name"],
            customer_type=row["customer_type"],
            site=row["site"],
            email=row["email"],
            active=bool(row["active"]),
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

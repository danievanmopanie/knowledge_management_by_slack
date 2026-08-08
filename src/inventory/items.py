"""Governed inventory item / SKU master data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.inventory.domain import InventoryDomainError, TrackingMode
from src.inventory.repository import InventoryRepository


@dataclass(frozen=True)
class InventoryItem:
    sku: str
    name: str
    tracking_mode: TrackingMode
    item_class: str
    manufacturer: str
    model: str
    active: bool
    default_reorder_point: int
    default_reorder_quantity: int
    created_by: str
    created_at: datetime


class ItemCatalogService:
    """Persist and validate inventory SKU master data."""

    ALLOWED_CLASSES = {
        "laptop",
        "desktop",
        "monitor",
        "mobile",
        "tablet",
        "peripheral",
        "component",
        "consumable",
        "network",
        "other",
    }

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_items (
                    sku TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    tracking_mode TEXT NOT NULL,
                    item_class TEXT NOT NULL,
                    manufacturer TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    default_reorder_point INTEGER NOT NULL DEFAULT 0,
                    default_reorder_quantity INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_inventory_items_active
                    ON inventory_items(active, item_class, sku);
                """
            )

    def create(
        self,
        *,
        sku: str,
        name: str,
        tracking_mode: str | TrackingMode,
        item_class: str,
        actor: str,
        manufacturer: str = "",
        model: str = "",
        default_reorder_point: int = 0,
        default_reorder_quantity: int = 0,
    ) -> InventoryItem:
        sku = sku.strip().upper()
        item_class = item_class.strip().lower()
        if not sku or not name.strip():
            raise InventoryDomainError("SKU and item name are required.")
        try:
            mode = tracking_mode if isinstance(tracking_mode, TrackingMode) else TrackingMode(str(tracking_mode).lower())
        except ValueError as exc:
            raise InventoryDomainError("tracking_mode must be 'serialized' or 'quantity'.") from exc
        if item_class not in self.ALLOWED_CLASSES:
            allowed = ", ".join(sorted(self.ALLOWED_CLASSES))
            raise InventoryDomainError(f"Item class must be one of: {allowed}.")
        if default_reorder_point < 0 or default_reorder_quantity < 0:
            raise InventoryDomainError("Default reorder values cannot be negative.")
        if mode == TrackingMode.SERIALIZED and (default_reorder_point or default_reorder_quantity):
            raise InventoryDomainError("Serialized items do not use quantity-stock reorder defaults.")
        if default_reorder_point and default_reorder_quantity == 0:
            raise InventoryDomainError("A positive reorder point requires a positive reorder quantity.")

        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            existing = conn.execute("SELECT sku FROM inventory_items WHERE sku=?", (sku,)).fetchone()
            if existing:
                raise InventoryDomainError(f"Inventory item already exists: {sku}")
            conn.execute(
                """INSERT INTO inventory_items(
                    sku,name,tracking_mode,item_class,manufacturer,model,active,
                    default_reorder_point,default_reorder_quantity,created_by,created_at
                ) VALUES (?,?,?,?,?,?,1,?,?,?,?,?)""".replace("?,?,?,?,?)", "?,?,?,?)"),
                (
                    sku,
                    name.strip(),
                    mode.value,
                    item_class,
                    manufacturer.strip(),
                    model.strip(),
                    default_reorder_point,
                    default_reorder_quantity,
                    actor,
                    now.isoformat(),
                ),
            )
        item = self.get(sku)
        if item is None:
            raise RuntimeError("Inventory item disappeared after creation.")
        return item

    def get(self, sku: str) -> InventoryItem | None:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_items WHERE sku=?",
                (sku.strip().upper(),),
            ).fetchone()
        return self._from_row(row) if row else None

    def require_active(self, sku: str) -> InventoryItem:
        item = self.get(sku)
        if item is None:
            raise InventoryDomainError(f"Unknown inventory SKU: {sku}")
        if not item.active:
            raise InventoryDomainError(f"Inventory SKU is inactive: {item.sku}")
        return item

    def validate_po_item(self, *, sku: str, tracking_mode: TrackingMode, model: str = "") -> InventoryItem:
        item = self.require_active(sku)
        if item.tracking_mode != tracking_mode:
            raise InventoryDomainError(
                f"PO tracking mode for {item.sku} is {tracking_mode.value}, but the item catalog requires {item.tracking_mode.value}."
            )
        if item.model and model and item.model.casefold() != model.strip().casefold():
            raise InventoryDomainError(
                f"PO model for {item.sku} is '{model}', but the item catalog model is '{item.model}'."
            )
        return item

    def list(self, *, include_inactive: bool = False, item_class: str = "") -> list[InventoryItem]:
        clauses: list[str] = []
        params: list[object] = []
        if not include_inactive:
            clauses.append("active=1")
        if item_class:
            clauses.append("item_class=?")
            params.append(item_class.strip().lower())
        sql = "SELECT * FROM inventory_items"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY item_class, sku"
        with self.repository._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._from_row(row) for row in rows]

    def set_active(self, sku: str, *, active: bool) -> InventoryItem:
        item = self.get(sku)
        if item is None:
            raise InventoryDomainError(f"Unknown inventory SKU: {sku}")
        if not active:
            with self.repository._connect() as conn:
                open_po = int(
                    conn.execute(
                        """SELECT COUNT(*) FROM inventory_po_lines l
                        JOIN inventory_purchase_orders p ON p.purchase_order_id=l.purchase_order_id
                        WHERE l.sku=? AND l.quantity_received < l.quantity_ordered""",
                        (item.sku,),
                    ).fetchone()[0]
                )
                stock = int(
                    conn.execute(
                        "SELECT COALESCE(SUM(on_hand),0) FROM inventory_stock_balances WHERE sku=?",
                        (item.sku,),
                    ).fetchone()[0]
                )
                live_assets = int(
                    conn.execute(
                        """SELECT COUNT(*) FROM inventory_assets
                        WHERE sku=? AND status NOT IN ('retired','disposed')""",
                        (item.sku,),
                    ).fetchone()[0]
                )
            if open_po or stock or live_assets:
                raise InventoryDomainError(
                    f"Cannot deactivate {item.sku}; open PO lines={open_po}, stock on hand={stock}, active assets={live_assets}."
                )
        with self.repository.transaction() as conn:
            conn.execute("UPDATE inventory_items SET active=? WHERE sku=?", (1 if active else 0, item.sku))
        updated = self.get(item.sku)
        if updated is None:
            raise RuntimeError("Inventory item disappeared after status update.")
        return updated

    def apply_default_reorder_rule(self, sku: str, *, location_id: str) -> bool:
        item = self.require_active(sku)
        if item.tracking_mode != TrackingMode.QUANTITY or item.default_reorder_quantity <= 0:
            return False
        with self.repository.transaction() as conn:
            conn.execute(
                """INSERT INTO inventory_reorder_rules(sku,location_id,reorder_point,reorder_quantity)
                VALUES (?,?,?,?)
                ON CONFLICT(sku,location_id) DO NOTHING""",
                (
                    item.sku,
                    location_id,
                    item.default_reorder_point,
                    item.default_reorder_quantity,
                ),
            )
        return True

    @staticmethod
    def _from_row(row) -> InventoryItem:
        return InventoryItem(
            sku=row["sku"],
            name=row["name"],
            tracking_mode=TrackingMode(row["tracking_mode"]),
            item_class=row["item_class"],
            manufacturer=row["manufacturer"],
            model=row["model"],
            active=bool(row["active"]),
            default_reorder_point=int(row["default_reorder_point"]),
            default_reorder_quantity=int(row["default_reorder_quantity"]),
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

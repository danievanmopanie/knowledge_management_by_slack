"""Governed physical storage-location hierarchy for inventory operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.inventory.domain import InventoryDomainError
from src.inventory.repository import InventoryRepository


VALID_LOCATION_TYPES = {"site", "storeroom", "cage", "shelf", "bin", "receiving", "repair", "quarantine"}


@dataclass(frozen=True)
class InventoryLocation:
    location_id: str
    name: str
    site: str
    location_type: str
    parent_id: str = ""
    active: bool = True
    created_by: str = ""
    created_at: datetime | None = None


class LocationService:
    """Persist and validate the physical hierarchy used by stock and assets."""

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_locations (
                    location_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    site TEXT NOT NULL,
                    location_type TEXT NOT NULL,
                    parent_id TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_inventory_locations_parent
                    ON inventory_locations(parent_id);
                CREATE INDEX IF NOT EXISTS idx_inventory_locations_site
                    ON inventory_locations(site, active);
                """
            )

    def create(
        self,
        *,
        location_id: str,
        name: str,
        site: str,
        location_type: str,
        actor: str,
        parent_id: str = "",
    ) -> InventoryLocation:
        location_id = location_id.strip().upper()
        site = site.strip().upper()
        location_type = location_type.strip().lower()
        if not location_id or not name.strip() or not site:
            raise InventoryDomainError("Location id, name and site are required.")
        if location_type not in VALID_LOCATION_TYPES:
            raise InventoryDomainError(
                "Location type must be one of: " + ", ".join(sorted(VALID_LOCATION_TYPES)) + "."
            )

        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            existing = conn.execute(
                "SELECT 1 FROM inventory_locations WHERE location_id=?",
                (location_id,),
            ).fetchone()
            if existing:
                raise InventoryDomainError(f"Location already exists: {location_id}")

            if parent_id:
                parent_id = parent_id.strip().upper()
                parent = conn.execute(
                    "SELECT * FROM inventory_locations WHERE location_id=? AND active=1",
                    (parent_id,),
                ).fetchone()
                if parent is None:
                    raise InventoryDomainError(f"Unknown or inactive parent location: {parent_id}")
                if parent["site"] != site:
                    raise InventoryDomainError("Child location must belong to the same site as its parent.")

            conn.execute(
                """INSERT INTO inventory_locations(
                    location_id,name,site,location_type,parent_id,active,created_by,created_at
                ) VALUES (?,?,?,?,?,1,?,?)""",
                (location_id, name.strip(), site, location_type, parent_id, actor, now.isoformat()),
            )

        return InventoryLocation(
            location_id=location_id,
            name=name.strip(),
            site=site,
            location_type=location_type,
            parent_id=parent_id,
            active=True,
            created_by=actor,
            created_at=now,
        )

    def get(self, location_id: str) -> InventoryLocation | None:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_locations WHERE location_id=?",
                (location_id.strip().upper(),),
            ).fetchone()
        if row is None:
            return None
        return InventoryLocation(
            location_id=row["location_id"],
            name=row["name"],
            site=row["site"],
            location_type=row["location_type"],
            parent_id=row["parent_id"],
            active=bool(row["active"]),
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def require_active(self, location_id: str) -> InventoryLocation:
        location = self.get(location_id)
        if location is None:
            raise InventoryDomainError(f"Unknown inventory location: {location_id}")
        if not location.active:
            raise InventoryDomainError(f"Inventory location is inactive: {location.location_id}")
        return location

    def set_active(self, location_id: str, *, active: bool) -> None:
        location_id = location_id.strip().upper()
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM inventory_locations WHERE location_id=?",
                (location_id,),
            ).fetchone()
            if row is None:
                raise InventoryDomainError(f"Unknown inventory location: {location_id}")
            if not active:
                children = conn.execute(
                    "SELECT COUNT(*) FROM inventory_locations WHERE parent_id=? AND active=1",
                    (location_id,),
                ).fetchone()[0]
                if children:
                    raise InventoryDomainError("Deactivate active child locations first.")
                balances = conn.execute(
                    """SELECT COUNT(*) FROM inventory_stock_balances
                    WHERE location_id=? AND (on_hand<>0 OR reserved<>0)""",
                    (location_id,),
                ).fetchone()[0]
                assets = conn.execute(
                    "SELECT COUNT(*) FROM inventory_assets WHERE location_id=? AND status NOT IN ('disposed')",
                    (location_id,),
                ).fetchone()[0]
                if balances or assets:
                    raise InventoryDomainError("Cannot deactivate a location that still contains inventory.")
            conn.execute(
                "UPDATE inventory_locations SET active=? WHERE location_id=?",
                (1 if active else 0, location_id),
            )

    def list(self, *, site: str = "", active_only: bool = True) -> list[InventoryLocation]:
        clauses: list[str] = []
        params: list[str | int] = []
        if site:
            clauses.append("site=?")
            params.append(site.strip().upper())
        if active_only:
            clauses.append("active=1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.repository._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_locations" + where + " ORDER BY site,parent_id,location_id",
                params,
            ).fetchall()
        return [
            InventoryLocation(
                location_id=row["location_id"],
                name=row["name"],
                site=row["site"],
                location_type=row["location_type"],
                parent_id=row["parent_id"],
                active=bool(row["active"]),
                created_by=row["created_by"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def path(self, location_id: str) -> list[InventoryLocation]:
        current = self.require_active(location_id)
        result = [current]
        visited = {current.location_id}
        while current.parent_id:
            parent = self.get(current.parent_id)
            if parent is None:
                raise InventoryDomainError(f"Broken location hierarchy at {current.parent_id}")
            if parent.location_id in visited:
                raise InventoryDomainError("Circular inventory location hierarchy detected.")
            visited.add(parent.location_id)
            result.append(parent)
            current = parent
        result.reverse()
        return result

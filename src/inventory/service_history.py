"""Auditable repair/RMA/service history for serialized inventory assets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from src.inventory.domain import AssetLifecycle, InventoryDomainError
from src.inventory.repository import InventoryRepository
from src.inventory.suppliers import SupplierService


@dataclass(frozen=True)
class AssetServiceCase:
    service_id: str
    asset_id: str
    service_type: str
    supplier_id: str
    external_reference: str
    status: str
    opened_by: str
    opened_at: datetime
    completed_by: str = ""
    completed_at: datetime | None = None
    outcome: str = ""
    cost: float = 0.0
    note: str = ""


@dataclass(frozen=True)
class AssetServiceEvent:
    event_id: str
    service_id: str
    action: str
    actor: str
    at: datetime
    detail: str


class AssetServiceHistoryService:
    """Manage serialized asset service cases without hiding lifecycle transitions."""

    ALLOWED_TYPES = {"repair", "rma", "warranty_claim", "inspection"}
    SERVICEABLE_STATUSES = {AssetLifecycle.REPAIR, AssetLifecycle.QUARANTINE}

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self.suppliers = SupplierService(repository)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_asset_service_cases (
                    service_id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL,
                    service_type TEXT NOT NULL,
                    supplier_id TEXT NOT NULL DEFAULT '',
                    external_reference TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    opened_by TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    completed_by TEXT NOT NULL DEFAULT '',
                    completed_at TEXT,
                    outcome TEXT NOT NULL DEFAULT '',
                    cost REAL NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (asset_id) REFERENCES inventory_assets(asset_id)
                );
                CREATE INDEX IF NOT EXISTS idx_inventory_service_asset_status
                    ON inventory_asset_service_cases(asset_id, status, opened_at);
                CREATE TABLE IF NOT EXISTS inventory_asset_service_events (
                    event_id TEXT PRIMARY KEY,
                    service_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    at TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (service_id)
                        REFERENCES inventory_asset_service_cases(service_id)
                );
                CREATE INDEX IF NOT EXISTS idx_inventory_service_events_case
                    ON inventory_asset_service_events(service_id, at);
                """
            )

    def open_case(
        self,
        *,
        asset_id: str,
        service_type: str,
        actor: str,
        supplier_id: str = "",
        external_reference: str = "",
        note: str = "",
    ) -> AssetServiceCase:
        service_type = service_type.strip().lower()
        if service_type not in self.ALLOWED_TYPES:
            allowed = ", ".join(sorted(self.ALLOWED_TYPES))
            raise InventoryDomainError(f"Service type must be one of: {allowed}.")
        asset = self.repository.load_asset(asset_id)
        if asset is None:
            raise InventoryDomainError(f"Unknown serialized asset: {asset_id}")
        if asset.status == AssetLifecycle.DISPOSED:
            raise InventoryDomainError("Disposed assets cannot enter a service case.")
        if service_type != "inspection" and asset.status not in self.SERVICEABLE_STATUSES:
            raise InventoryDomainError(
                f"Asset {asset.asset_id} must be in repair or quarantine before opening a {service_type} case."
            )
        governed_supplier = ""
        if supplier_id:
            governed_supplier = self.suppliers.require_active(supplier_id).supplier_id
        if service_type in {"rma", "warranty_claim"} and not governed_supplier:
            raise InventoryDomainError(f"{service_type} requires a governed supplier.")

        now = datetime.now(timezone.utc)
        service_id = f"SVC-{uuid4().hex[:10]}"
        with self.repository.transaction() as conn:
            existing = conn.execute(
                """SELECT service_id FROM inventory_asset_service_cases
                WHERE asset_id=? AND status='open'""",
                (asset.asset_id,),
            ).fetchone()
            if existing:
                raise InventoryDomainError(
                    f"Asset {asset.asset_id} already has an open service case: {existing['service_id']}"
                )
            conn.execute(
                """INSERT INTO inventory_asset_service_cases(
                    service_id,asset_id,service_type,supplier_id,external_reference,
                    status,opened_by,opened_at,note
                ) VALUES (?,?,?,?,?,'open',?,?,?)""",
                (
                    service_id,
                    asset.asset_id,
                    service_type,
                    governed_supplier,
                    external_reference.strip(),
                    actor,
                    now.isoformat(),
                    note.strip(),
                ),
            )
            self._add_event(
                conn,
                service_id=service_id,
                action="opened",
                actor=actor,
                at=now,
                detail=note.strip(),
            )
        case = self.get(service_id)
        if case is None:
            raise RuntimeError("Service case disappeared after creation.")
        return case

    def complete(
        self,
        service_id: str,
        *,
        actor: str,
        outcome: str,
        cost: float = 0.0,
        note: str = "",
    ) -> AssetServiceCase:
        if not outcome.strip():
            raise InventoryDomainError("Service outcome is required.")
        if cost < 0:
            raise InventoryDomainError("Service cost cannot be negative.")
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_asset_service_cases WHERE service_id=?",
                (service_id,),
            ).fetchone()
            if row is None or row["status"] != "open":
                raise InventoryDomainError("Unknown or inactive service case.")
            conn.execute(
                """UPDATE inventory_asset_service_cases
                SET status='completed',completed_by=?,completed_at=?,outcome=?,cost=?,note=?
                WHERE service_id=?""",
                (
                    actor,
                    now.isoformat(),
                    outcome.strip(),
                    float(cost),
                    note.strip() or row["note"],
                    service_id,
                ),
            )
            self._add_event(
                conn,
                service_id=service_id,
                action="completed",
                actor=actor,
                at=now,
                detail=f"Outcome: {outcome.strip()}; cost: {float(cost):.2f}. {note.strip()}".strip(),
            )
        case = self.get(service_id)
        if case is None:
            raise RuntimeError("Service case disappeared after completion.")
        return case

    def cancel(self, service_id: str, *, actor: str, note: str) -> AssetServiceCase:
        if not note.strip():
            raise InventoryDomainError("Cancellation note is required.")
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT status FROM inventory_asset_service_cases WHERE service_id=?",
                (service_id,),
            ).fetchone()
            if row is None or row["status"] != "open":
                raise InventoryDomainError("Unknown or inactive service case.")
            conn.execute(
                """UPDATE inventory_asset_service_cases
                SET status='cancelled',completed_by=?,completed_at=?,outcome='cancelled',note=?
                WHERE service_id=?""",
                (actor, now.isoformat(), note.strip(), service_id),
            )
            self._add_event(
                conn,
                service_id=service_id,
                action="cancelled",
                actor=actor,
                at=now,
                detail=note.strip(),
            )
        case = self.get(service_id)
        if case is None:
            raise RuntimeError("Service case disappeared after cancellation.")
        return case

    def get(self, service_id: str) -> AssetServiceCase | None:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_asset_service_cases WHERE service_id=?",
                (service_id,),
            ).fetchone()
        return self._case_from_row(row) if row else None

    def cases_for_asset(self, asset_id: str) -> list[AssetServiceCase]:
        asset = self.repository.load_asset(asset_id)
        if asset is None:
            raise InventoryDomainError(f"Unknown serialized asset: {asset_id}")
        with self.repository._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM inventory_asset_service_cases
                WHERE asset_id=? ORDER BY opened_at DESC, service_id DESC""",
                (asset.asset_id,),
            ).fetchall()
        return [self._case_from_row(row) for row in rows]

    def open_cases(self) -> list[AssetServiceCase]:
        with self.repository._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM inventory_asset_service_cases
                WHERE status='open' ORDER BY opened_at, service_id"""
            ).fetchall()
        return [self._case_from_row(row) for row in rows]

    def events(self, service_id: str) -> list[AssetServiceEvent]:
        if self.get(service_id) is None:
            raise InventoryDomainError(f"Unknown service case: {service_id}")
        with self.repository._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM inventory_asset_service_events
                WHERE service_id=? ORDER BY at, event_id""",
                (service_id,),
            ).fetchall()
        return [
            AssetServiceEvent(
                event_id=row["event_id"],
                service_id=row["service_id"],
                action=row["action"],
                actor=row["actor"],
                at=datetime.fromisoformat(row["at"]),
                detail=row["detail"],
            )
            for row in rows
        ]

    def total_service_cost(self, asset_id: str) -> float:
        asset = self.repository.load_asset(asset_id)
        if asset is None:
            raise InventoryDomainError(f"Unknown serialized asset: {asset_id}")
        with self.repository._connect() as conn:
            value = conn.execute(
                """SELECT COALESCE(SUM(cost),0) FROM inventory_asset_service_cases
                WHERE asset_id=? AND status='completed'""",
                (asset.asset_id,),
            ).fetchone()[0]
        return float(value or 0)

    @staticmethod
    def _add_event(conn, *, service_id: str, action: str, actor: str, at: datetime, detail: str) -> None:
        conn.execute(
            """INSERT INTO inventory_asset_service_events(
                event_id,service_id,action,actor,at,detail
            ) VALUES (?,?,?,?,?,?)""",
            (str(uuid4()), service_id, action, actor, at.isoformat(), detail),
        )

    @staticmethod
    def _case_from_row(row) -> AssetServiceCase:
        return AssetServiceCase(
            service_id=row["service_id"],
            asset_id=row["asset_id"],
            service_type=row["service_type"],
            supplier_id=row["supplier_id"],
            external_reference=row["external_reference"],
            status=row["status"],
            opened_by=row["opened_by"],
            opened_at=datetime.fromisoformat(row["opened_at"]),
            completed_by=row["completed_by"],
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
            outcome=row["outcome"],
            cost=float(row["cost"]),
            note=row["note"],
        )

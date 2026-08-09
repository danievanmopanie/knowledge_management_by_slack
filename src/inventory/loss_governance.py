"""Govern confirmed loss, financial write-off and later asset recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from src.inventory.domain import InventoryDomainError
from src.inventory.locations import LocationService
from src.inventory.lost_investigations import LostAssetInvestigationService
from src.inventory.repository import InventoryRepository


@dataclass(frozen=True)
class AssetLossCase:
    loss_id: str
    investigation_id: str
    asset_id: str
    status: str
    requested_by: str
    requested_at: datetime
    approved_by: str = ""
    approved_at: datetime | None = None
    financial_reference: str = ""
    writeoff_reference: str = ""
    written_off_by: str = ""
    written_off_at: datetime | None = None
    recovery_reference: str = ""
    recovery_location: str = ""
    recovered_by: str = ""
    recovered_at: datetime | None = None
    note: str = ""


@dataclass(frozen=True)
class AssetLossEvent:
    event_id: str
    loss_id: str
    action: str
    actor: str
    note: str
    at: datetime


class AssetLossGovernanceService:
    """Separate approval and financial write-off from physical asset lifecycle state."""

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self.investigations = LostAssetInvestigationService(repository)
        self.locations = LocationService(repository)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_asset_loss_cases (
                    loss_id TEXT PRIMARY KEY,
                    investigation_id TEXT NOT NULL UNIQUE,
                    asset_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending_approval',
                    requested_by TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    approved_by TEXT NOT NULL DEFAULT '',
                    approved_at TEXT,
                    financial_reference TEXT NOT NULL DEFAULT '',
                    writeoff_reference TEXT NOT NULL DEFAULT '',
                    written_off_by TEXT NOT NULL DEFAULT '',
                    written_off_at TEXT,
                    recovery_reference TEXT NOT NULL DEFAULT '',
                    recovery_location TEXT NOT NULL DEFAULT '',
                    recovered_by TEXT NOT NULL DEFAULT '',
                    recovered_at TEXT,
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS inventory_asset_loss_events (
                    event_id TEXT PRIMARY KEY,
                    loss_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL,
                    at TEXT NOT NULL,
                    FOREIGN KEY (loss_id) REFERENCES inventory_asset_loss_cases(loss_id)
                );
                CREATE INDEX IF NOT EXISTS idx_asset_loss_status
                    ON inventory_asset_loss_cases(status, requested_at);
                CREATE INDEX IF NOT EXISTS idx_asset_loss_asset
                    ON inventory_asset_loss_cases(asset_id, requested_at);
                """
            )

    def request(self, investigation_id: str, *, actor: str, note: str) -> AssetLossCase:
        investigation = self.investigations.require(investigation_id)
        if investigation.status != "closed" or investigation.outcome != "confirmed_lost":
            raise InventoryDomainError(
                "A loss case requires a closed lost-asset investigation with outcome confirmed_lost."
            )
        if not note.strip():
            raise InventoryDomainError("Loss request requires a note.")
        with self.repository._connect() as conn:
            existing = conn.execute(
                "SELECT loss_id FROM inventory_asset_loss_cases WHERE investigation_id=?",
                (investigation_id,),
            ).fetchone()
        if existing:
            return self.require(existing["loss_id"])
        now = datetime.now(timezone.utc)
        loss_id = f"LOS-{uuid4().hex[:10]}"
        with self.repository.transaction() as conn:
            conn.execute(
                """INSERT INTO inventory_asset_loss_cases(
                    loss_id,investigation_id,asset_id,status,requested_by,requested_at,note
                ) VALUES (?,?,?,'pending_approval',?,?,?)""",
                (
                    loss_id,
                    investigation.investigation_id,
                    investigation.asset_id,
                    actor,
                    now.isoformat(),
                    note.strip(),
                ),
            )
            self._event(conn, loss_id, "requested", actor, note.strip(), now)
        return self.require(loss_id)

    def approve(
        self,
        loss_id: str,
        *,
        actor: str,
        financial_reference: str,
        note: str,
    ) -> AssetLossCase:
        item = self.require(loss_id)
        if item.status != "pending_approval":
            raise InventoryDomainError(f"Loss case is not awaiting approval: {loss_id}")
        if actor == item.requested_by:
            raise InventoryDomainError("Loss approval must be performed by someone other than the requester.")
        if not financial_reference.strip():
            raise InventoryDomainError("Loss approval requires a financial/reference number.")
        if not note.strip():
            raise InventoryDomainError("Loss approval requires a note.")
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            conn.execute(
                """UPDATE inventory_asset_loss_cases
                SET status='approved',approved_by=?,approved_at=?,financial_reference=?
                WHERE loss_id=?""",
                (actor, now.isoformat(), financial_reference.strip(), loss_id),
            )
            self._event(
                conn,
                loss_id,
                "approved",
                actor,
                f"{financial_reference.strip()}: {note.strip()}",
                now,
            )
        return self.require(loss_id)

    def write_off(
        self,
        loss_id: str,
        *,
        actor: str,
        writeoff_reference: str,
        note: str,
    ) -> AssetLossCase:
        item = self.require(loss_id)
        if item.status != "approved":
            raise InventoryDomainError("Asset write-off requires an approved loss case.")
        if not writeoff_reference.strip():
            raise InventoryDomainError("Asset write-off requires a write-off reference.")
        if not note.strip():
            raise InventoryDomainError("Asset write-off requires a note.")
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            conn.execute(
                """UPDATE inventory_asset_loss_cases
                SET status='written_off',writeoff_reference=?,written_off_by=?,written_off_at=?
                WHERE loss_id=?""",
                (writeoff_reference.strip(), actor, now.isoformat(), loss_id),
            )
            self._event(
                conn,
                loss_id,
                "written_off",
                actor,
                f"{writeoff_reference.strip()}: {note.strip()}",
                now,
            )
        return self.require(loss_id)

    def recover(
        self,
        loss_id: str,
        *,
        actor: str,
        location_id: str,
        recovery_reference: str,
        note: str,
    ) -> AssetLossCase:
        item = self.require(loss_id)
        if item.status not in {"pending_approval", "approved", "written_off"}:
            raise InventoryDomainError(
                "Only an active confirmed-loss case can be recovered."
            )
        location = self.locations.require_active(location_id)
        if not recovery_reference.strip():
            raise InventoryDomainError("Asset recovery requires a recovery/reversal reference.")
        if not note.strip():
            raise InventoryDomainError("Asset recovery requires a note.")
        asset = self.repository.load_asset(item.asset_id)
        if asset is None:
            raise InventoryDomainError(f"Unknown serialized asset: {item.asset_id}")
        if asset.status.value != "lost":
            raise InventoryDomainError(
                f"Asset {item.asset_id} is not in confirmed-lost lifecycle state."
            )
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            conn.execute(
                """UPDATE inventory_assets
                SET status='quarantine',location_id=?,assigned_to='',customer_ref='',
                    allocation_type=NULL,return_due_at=NULL,
                    metadata_json=json_set(
                        metadata_json,
                        '$.recovery_reference', ?,
                        '$.recovered_at', ?,
                        '$.recovered_by', ?,
                        '$.recovery_location', ?
                    )
                WHERE asset_id=?""",
                (
                    location.location_id,
                    recovery_reference.strip(),
                    now.isoformat(),
                    actor,
                    location.location_id,
                    item.asset_id,
                ),
            )
            conn.execute(
                """INSERT INTO inventory_asset_movements(
                    movement_id,asset_id,from_status,to_status,actor,at,
                    from_location,to_location,customer_ref,note
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid4()),
                    item.asset_id,
                    "lost",
                    "quarantine",
                    actor,
                    now.isoformat(),
                    asset.location_id,
                    location.location_id,
                    "",
                    f"Recovered under {recovery_reference.strip()}: {note.strip()}",
                ),
            )
            conn.execute(
                """UPDATE inventory_asset_loss_cases
                SET status='recovered',recovery_reference=?,recovery_location=?,
                    recovered_by=?,recovered_at=?
                WHERE loss_id=?""",
                (
                    recovery_reference.strip(),
                    location.location_id,
                    actor,
                    now.isoformat(),
                    loss_id,
                ),
            )
            self._event(
                conn,
                loss_id,
                "recovered",
                actor,
                f"{recovery_reference.strip()} @ {location.location_id}: {note.strip()}",
                now,
            )
        return self.require(loss_id)

    def disposal_allowed(self, asset_id: str) -> bool:
        """Confirmed-lost assets require a completed write-off before final disposal."""
        with self.repository._connect() as conn:
            confirmed = conn.execute(
                """SELECT 1 FROM inventory_lost_asset_investigations
                WHERE asset_id=? AND status='closed' AND outcome='confirmed_lost'
                LIMIT 1""",
                (asset_id,),
            ).fetchone()
            if not confirmed:
                return True
            latest = conn.execute(
                """SELECT status FROM inventory_asset_loss_cases
                WHERE asset_id=? ORDER BY requested_at DESC LIMIT 1""",
                (asset_id,),
            ).fetchone()
        return bool(latest and latest["status"] == "written_off")

    def list(self, *, status: str = "all", asset_id: str = "") -> list[AssetLossCase]:
        clauses: list[str] = []
        params: list[str] = []
        if status != "all":
            allowed = {"pending_approval", "approved", "written_off", "recovered"}
            if status not in allowed:
                raise InventoryDomainError("Unknown loss-case status filter.")
            clauses.append("status=?")
            params.append(status)
        if asset_id:
            clauses.append("asset_id=?")
            params.append(asset_id)
        sql = "SELECT * FROM inventory_asset_loss_cases"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY requested_at,loss_id"
        with self.repository._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row(row) for row in rows]

    def events(self, loss_id: str) -> list[AssetLossEvent]:
        self.require(loss_id)
        with self.repository._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_asset_loss_events WHERE loss_id=? ORDER BY at,event_id",
                (loss_id,),
            ).fetchall()
        return [
            AssetLossEvent(
                event_id=row["event_id"],
                loss_id=row["loss_id"],
                action=row["action"],
                actor=row["actor"],
                note=row["note"],
                at=datetime.fromisoformat(row["at"]),
            )
            for row in rows
        ]

    def require(self, loss_id: str) -> AssetLossCase:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_asset_loss_cases WHERE loss_id=?",
                (loss_id,),
            ).fetchone()
        if not row:
            raise InventoryDomainError(f"Unknown asset loss case: {loss_id}")
        return self._row(row)

    @staticmethod
    def _event(conn, loss_id: str, action: str, actor: str, note: str, at: datetime) -> None:
        conn.execute(
            """INSERT INTO inventory_asset_loss_events(event_id,loss_id,action,actor,note,at)
            VALUES (?,?,?,?,?,?)""",
            (str(uuid4()), loss_id, action, actor, note, at.isoformat()),
        )

    @staticmethod
    def _row(row) -> AssetLossCase:
        return AssetLossCase(
            loss_id=row["loss_id"],
            investigation_id=row["investigation_id"],
            asset_id=row["asset_id"],
            status=row["status"],
            requested_by=row["requested_by"],
            requested_at=datetime.fromisoformat(row["requested_at"]),
            approved_by=row["approved_by"],
            approved_at=datetime.fromisoformat(row["approved_at"]) if row["approved_at"] else None,
            financial_reference=row["financial_reference"],
            writeoff_reference=row["writeoff_reference"],
            written_off_by=row["written_off_by"],
            written_off_at=datetime.fromisoformat(row["written_off_at"]) if row["written_off_at"] else None,
            recovery_reference=row["recovery_reference"],
            recovery_location=row["recovery_location"],
            recovered_by=row["recovered_by"],
            recovered_at=datetime.fromisoformat(row["recovered_at"]) if row["recovered_at"] else None,
            note=row["note"],
        )

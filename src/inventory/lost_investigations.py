"""Track lost-asset investigations raised from physical-audit corrections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from src.inventory.domain import InventoryDomainError
from src.inventory.repository import InventoryRepository


@dataclass(frozen=True)
class LostAssetInvestigation:
    investigation_id: str
    correction_id: str
    finding_id: str
    asset_id: str
    owner: str
    status: str
    opened_at: datetime
    opened_by: str
    outcome: str = ""
    closed_at: datetime | None = None
    closed_by: str = ""


@dataclass(frozen=True)
class LostAssetInvestigationEvent:
    event_id: str
    investigation_id: str
    action: str
    actor: str
    note: str
    at: datetime


class LostAssetInvestigationService:
    OUTCOMES = {"found", "confirmed_lost", "transferred_custody", "other"}

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_lost_asset_investigations (
                    investigation_id TEXT PRIMARY KEY,
                    correction_id TEXT NOT NULL UNIQUE,
                    finding_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    opened_at TEXT NOT NULL,
                    opened_by TEXT NOT NULL,
                    outcome TEXT NOT NULL DEFAULT '',
                    closed_at TEXT,
                    closed_by TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS inventory_lost_asset_investigation_events (
                    event_id TEXT PRIMARY KEY,
                    investigation_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL,
                    at TEXT NOT NULL,
                    FOREIGN KEY (investigation_id)
                        REFERENCES inventory_lost_asset_investigations(investigation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_lost_asset_investigations_status
                    ON inventory_lost_asset_investigations(status, owner, opened_at);
                CREATE INDEX IF NOT EXISTS idx_lost_asset_investigation_events
                    ON inventory_lost_asset_investigation_events(investigation_id, at);
                """
            )

    def assign(self, investigation_id: str, *, owner: str, actor: str) -> LostAssetInvestigation:
        if not owner.strip():
            raise InventoryDomainError("Lost-asset investigation owner is required.")
        item = self.require_open(investigation_id)
        with self.repository.transaction() as conn:
            conn.execute(
                "UPDATE inventory_lost_asset_investigations SET owner=? WHERE investigation_id=?",
                (owner.strip(), item.investigation_id),
            )
            self._event(conn, item.investigation_id, "assigned", actor, f"Owner: {owner.strip()}")
        return self.require(item.investigation_id)

    def add_note(self, investigation_id: str, *, note: str, actor: str) -> None:
        if not note.strip():
            raise InventoryDomainError("Investigation note is required.")
        item = self.require_open(investigation_id)
        with self.repository.transaction() as conn:
            self._event(conn, item.investigation_id, "note", actor, note.strip())

    def close(
        self,
        investigation_id: str,
        *,
        outcome: str,
        note: str,
        actor: str,
    ) -> LostAssetInvestigation:
        outcome = outcome.strip().lower()
        if outcome not in self.OUTCOMES:
            raise InventoryDomainError(
                f"Investigation outcome must be one of: {', '.join(sorted(self.OUTCOMES))}."
            )
        if not note.strip():
            raise InventoryDomainError("Closing an investigation requires a note.")
        item = self.require_open(investigation_id)
        now = datetime.now().astimezone()
        with self.repository.transaction() as conn:
            conn.execute(
                """UPDATE inventory_lost_asset_investigations
                SET status='closed',outcome=?,closed_at=?,closed_by=?
                WHERE investigation_id=?""",
                (outcome, now.isoformat(), actor, item.investigation_id),
            )
            self._event(conn, item.investigation_id, "closed", actor, f"{outcome}: {note.strip()}", at=now)
            if outcome == "found":
                conn.execute(
                    """UPDATE inventory_assets
                    SET metadata_json=json_remove(metadata_json,
                        '$.audit_investigation_status',
                        '$.audit_investigation_finding',
                        '$.audit_investigation_note')
                    WHERE asset_id=?""",
                    (item.asset_id,),
                )
        return self.require(item.investigation_id)

    def list(self, *, status: str = "open", owner: str = "") -> list[LostAssetInvestigation]:
        clauses = []
        params: list[str] = []
        if status != "all":
            if status not in {"open", "closed"}:
                raise InventoryDomainError("Investigation status must be open, closed, or all.")
            clauses.append("status=?")
            params.append(status)
        if owner:
            clauses.append("owner=?")
            params.append(owner)
        sql = "SELECT * FROM inventory_lost_asset_investigations"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY opened_at, investigation_id"
        with self.repository._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row(row) for row in rows]

    def events(self, investigation_id: str) -> list[LostAssetInvestigationEvent]:
        self.require(investigation_id)
        with self.repository._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM inventory_lost_asset_investigation_events
                WHERE investigation_id=? ORDER BY at,event_id""",
                (investigation_id,),
            ).fetchall()
        return [
            LostAssetInvestigationEvent(
                event_id=row["event_id"],
                investigation_id=row["investigation_id"],
                action=row["action"],
                actor=row["actor"],
                note=row["note"],
                at=datetime.fromisoformat(row["at"]),
            )
            for row in rows
        ]

    def require_open(self, investigation_id: str) -> LostAssetInvestigation:
        item = self.require(investigation_id)
        if item.status != "open":
            raise InventoryDomainError(f"Lost-asset investigation is not open: {investigation_id}")
        return item

    def require(self, investigation_id: str) -> LostAssetInvestigation:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_lost_asset_investigations WHERE investigation_id=?",
                (investigation_id,),
            ).fetchone()
        if not row:
            raise InventoryDomainError(f"Unknown lost-asset investigation: {investigation_id}")
        return self._row(row)

    @staticmethod
    def create_in_transaction(
        conn,
        *,
        correction_id: str,
        finding_id: str,
        asset_id: str,
        actor: str,
        note: str,
    ) -> str:
        investigation_id = f"INV-{uuid4().hex[:10]}"
        now = datetime.now().astimezone()
        conn.execute(
            """INSERT INTO inventory_lost_asset_investigations(
                investigation_id,correction_id,finding_id,asset_id,owner,status,
                opened_at,opened_by
            ) VALUES (?,?,?,?,?,'open',?,?)""",
            (investigation_id, correction_id, finding_id, asset_id, actor, now.isoformat(), actor),
        )
        conn.execute(
            """INSERT INTO inventory_lost_asset_investigation_events(
                event_id,investigation_id,action,actor,note,at
            ) VALUES (?,?,?,?,?,?)""",
            (str(uuid4()), investigation_id, "opened", actor, note, now.isoformat()),
        )
        return investigation_id

    @staticmethod
    def _event(conn, investigation_id: str, action: str, actor: str, note: str, *, at=None) -> None:
        at = at or datetime.now().astimezone()
        conn.execute(
            """INSERT INTO inventory_lost_asset_investigation_events(
                event_id,investigation_id,action,actor,note,at
            ) VALUES (?,?,?,?,?,?)""",
            (str(uuid4()), investigation_id, action, actor, note, at.isoformat()),
        )

    @staticmethod
    def _row(row) -> LostAssetInvestigation:
        return LostAssetInvestigation(
            investigation_id=row["investigation_id"],
            correction_id=row["correction_id"],
            finding_id=row["finding_id"],
            asset_id=row["asset_id"],
            owner=row["owner"],
            status=row["status"],
            opened_at=datetime.fromisoformat(row["opened_at"]),
            opened_by=row["opened_by"],
            outcome=row["outcome"],
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            closed_by=row["closed_by"],
        )

"""Durable findings and explicit resolution workflow for serialized-asset audits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from src.inventory.domain import InventoryDomainError
from src.inventory.repository import InventoryRepository
from src.inventory.serialized_audit import SerializedAssetAuditService


@dataclass(frozen=True)
class AssetAuditFinding:
    finding_id: str
    audit_id: str
    finding_type: str
    asset_id: str
    observed_value: str
    expected_location: str
    observed_location: str
    status: str
    created_at: datetime
    resolution_type: str = ""
    resolution_note: str = ""
    resolved_by: str = ""
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class AssetAuditFindingEvent:
    event_id: str
    finding_id: str
    action: str
    actor: str
    note: str
    at: datetime


class AssetAuditFindingService:
    """Materialize audit discrepancies and resolve them without implicit asset writes."""

    RESOLUTION_TYPES = {
        "relocated",
        "custody_confirmed",
        "ledger_verified",
        "lost_investigation",
        "false_scan",
        "other",
    }

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self.audits = SerializedAssetAuditService(repository)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_asset_audit_findings (
                    finding_id TEXT PRIMARY KEY,
                    audit_id TEXT NOT NULL,
                    finding_key TEXT NOT NULL,
                    finding_type TEXT NOT NULL,
                    asset_id TEXT NOT NULL DEFAULT '',
                    observed_value TEXT NOT NULL DEFAULT '',
                    expected_location TEXT NOT NULL DEFAULT '',
                    observed_location TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    resolution_type TEXT NOT NULL DEFAULT '',
                    resolution_note TEXT NOT NULL DEFAULT '',
                    resolved_by TEXT NOT NULL DEFAULT '',
                    resolved_at TEXT,
                    UNIQUE(audit_id, finding_key),
                    FOREIGN KEY (audit_id) REFERENCES inventory_asset_audits(audit_id)
                );
                CREATE TABLE IF NOT EXISTS inventory_asset_audit_finding_events (
                    event_id TEXT PRIMARY KEY,
                    finding_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    at TEXT NOT NULL,
                    FOREIGN KEY (finding_id)
                        REFERENCES inventory_asset_audit_findings(finding_id)
                );
                CREATE INDEX IF NOT EXISTS idx_asset_audit_findings_status
                    ON inventory_asset_audit_findings(status, finding_type, created_at);
                CREATE INDEX IF NOT EXISTS idx_asset_audit_finding_events
                    ON inventory_asset_audit_finding_events(finding_id, at);
                """
            )

    def materialize(self, audit_id: str, *, actor: str) -> list[AssetAuditFinding]:
        audit = self.audits.require(audit_id)
        if audit.status != "closed":
            raise InventoryDomainError("Audit findings can only be materialized after the audit is closed.")
        result = self.audits.result(audit_id)
        scans = self.audits.scans(audit_id)
        scan_by_asset = {scan.asset_id: scan for scan in scans if scan.asset_id}
        unknown_scans = [scan for scan in scans if scan.result == "unknown"]
        now = datetime.now(timezone.utc)

        with self.repository.transaction() as conn:
            for asset_id in result.missing_asset_ids:
                expected = conn.execute(
                    """SELECT expected_location FROM inventory_asset_audit_expected
                    WHERE audit_id=? AND asset_id=?""",
                    (audit_id, asset_id),
                ).fetchone()
                self._insert_finding(
                    conn,
                    audit_id=audit_id,
                    finding_key=f"missing:{asset_id}",
                    finding_type="missing",
                    asset_id=asset_id,
                    observed_value="",
                    expected_location=expected["expected_location"] if expected else audit.location_id,
                    observed_location="",
                    actor=actor,
                    now=now,
                )
            for asset_id in result.misplaced_asset_ids:
                scan = scan_by_asset[asset_id]
                self._insert_finding(
                    conn,
                    audit_id=audit_id,
                    finding_key=f"misplaced:{asset_id}",
                    finding_type="misplaced",
                    asset_id=asset_id,
                    observed_value=scan.scanned_value,
                    expected_location=scan.expected_location,
                    observed_location=scan.actual_location,
                    actor=actor,
                    now=now,
                )
            for scan in unknown_scans:
                self._insert_finding(
                    conn,
                    audit_id=audit_id,
                    finding_key=f"unknown:{scan.scanned_value}",
                    finding_type="unknown",
                    asset_id="",
                    observed_value=scan.scanned_value,
                    expected_location="",
                    observed_location=scan.actual_location,
                    actor=actor,
                    now=now,
                )
        return self.list(audit_id=audit_id)

    def resolve(
        self,
        finding_id: str,
        *,
        resolution_type: str,
        note: str,
        actor: str,
    ) -> AssetAuditFinding:
        resolution_type = resolution_type.strip().lower()
        if resolution_type not in self.RESOLUTION_TYPES:
            allowed = ", ".join(sorted(self.RESOLUTION_TYPES))
            raise InventoryDomainError(f"Audit finding resolution must be one of: {allowed}.")
        if not note.strip():
            raise InventoryDomainError("Audit finding resolution requires a note.")
        finding = self.require(finding_id)
        if finding.status != "open":
            raise InventoryDomainError(f"Audit finding is not open: {finding_id}")
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            conn.execute(
                """UPDATE inventory_asset_audit_findings
                SET status='resolved',resolution_type=?,resolution_note=?,resolved_by=?,resolved_at=?
                WHERE finding_id=?""",
                (resolution_type, note.strip(), actor, now.isoformat(), finding.finding_id),
            )
            self._event(
                conn,
                finding_id=finding.finding_id,
                action="resolved",
                actor=actor,
                note=f"{resolution_type}: {note.strip()}",
                at=now,
            )
        return self.require(finding.finding_id)

    def reopen(self, finding_id: str, *, note: str, actor: str) -> AssetAuditFinding:
        if not note.strip():
            raise InventoryDomainError("Reopening an audit finding requires a note.")
        finding = self.require(finding_id)
        if finding.status != "resolved":
            raise InventoryDomainError(f"Audit finding is not resolved: {finding_id}")
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            conn.execute(
                """UPDATE inventory_asset_audit_findings
                SET status='open',resolution_type='',resolution_note='',resolved_by='',resolved_at=NULL
                WHERE finding_id=?""",
                (finding.finding_id,),
            )
            self._event(
                conn,
                finding_id=finding.finding_id,
                action="reopened",
                actor=actor,
                note=note.strip(),
                at=now,
            )
        return self.require(finding.finding_id)

    def list(
        self,
        *,
        audit_id: str = "",
        status: str = "open",
    ) -> list[AssetAuditFinding]:
        clauses: list[str] = []
        params: list[str] = []
        if audit_id:
            clauses.append("audit_id=?")
            params.append(audit_id)
        if status != "all":
            if status not in {"open", "resolved"}:
                raise InventoryDomainError("Finding status must be open, resolved, or all.")
            clauses.append("status=?")
            params.append(status)
        sql = "SELECT * FROM inventory_asset_audit_findings"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at,finding_id"
        with self.repository._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._finding(row) for row in rows]

    def events(self, finding_id: str) -> list[AssetAuditFindingEvent]:
        self.require(finding_id)
        with self.repository._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM inventory_asset_audit_finding_events
                WHERE finding_id=? ORDER BY at,event_id""",
                (finding_id,),
            ).fetchall()
        return [
            AssetAuditFindingEvent(
                event_id=row["event_id"],
                finding_id=row["finding_id"],
                action=row["action"],
                actor=row["actor"],
                note=row["note"],
                at=datetime.fromisoformat(row["at"]),
            )
            for row in rows
        ]

    def require(self, finding_id: str) -> AssetAuditFinding:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_asset_audit_findings WHERE finding_id=?",
                (finding_id,),
            ).fetchone()
        if not row:
            raise InventoryDomainError(f"Unknown asset audit finding: {finding_id}")
        return self._finding(row)

    def _insert_finding(
        self,
        conn,
        *,
        audit_id: str,
        finding_key: str,
        finding_type: str,
        asset_id: str,
        observed_value: str,
        expected_location: str,
        observed_location: str,
        actor: str,
        now: datetime,
    ) -> None:
        finding_id = f"FND-{uuid4().hex[:10]}"
        cursor = conn.execute(
            """INSERT OR IGNORE INTO inventory_asset_audit_findings(
                finding_id,audit_id,finding_key,finding_type,asset_id,observed_value,
                expected_location,observed_location,status,created_at
            ) VALUES (?,?,?,?,?,?,?,?,'open',?)""",
            (
                finding_id,
                audit_id,
                finding_key,
                finding_type,
                asset_id,
                observed_value,
                expected_location,
                observed_location,
                now.isoformat(),
            ),
        )
        if cursor.rowcount:
            self._event(
                conn,
                finding_id=finding_id,
                action="created",
                actor=actor,
                note=f"Materialized from audit {audit_id}",
                at=now,
            )

    @staticmethod
    def _event(conn, *, finding_id: str, action: str, actor: str, note: str, at: datetime) -> None:
        conn.execute(
            """INSERT INTO inventory_asset_audit_finding_events(
                event_id,finding_id,action,actor,note,at
            ) VALUES (?,?,?,?,?,?)""",
            (str(uuid4()), finding_id, action, actor, note, at.isoformat()),
        )

    @staticmethod
    def _finding(row) -> AssetAuditFinding:
        return AssetAuditFinding(
            finding_id=row["finding_id"],
            audit_id=row["audit_id"],
            finding_type=row["finding_type"],
            asset_id=row["asset_id"],
            observed_value=row["observed_value"],
            expected_location=row["expected_location"],
            observed_location=row["observed_location"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolution_type=row["resolution_type"],
            resolution_note=row["resolution_note"],
            resolved_by=row["resolved_by"],
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        )

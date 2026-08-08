"""Physical audit workflow for serialized inventory assets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from src.inventory.domain import AssetLifecycle, InventoryDomainError
from src.inventory.locations import LocationService
from src.inventory.repository import InventoryRepository


@dataclass(frozen=True)
class AssetAuditSession:
    audit_id: str
    location_id: str
    status: str
    opened_by: str
    opened_at: datetime
    closed_by: str = ""
    closed_at: datetime | None = None
    note: str = ""


@dataclass(frozen=True)
class AssetAuditScan:
    scan_id: str
    audit_id: str
    scanned_value: str
    asset_id: str
    result: str
    expected_location: str
    actual_location: str
    scanned_by: str
    scanned_at: datetime


@dataclass(frozen=True)
class AssetAuditResult:
    audit_id: str
    location_id: str
    expected_count: int
    matched_asset_ids: tuple[str, ...]
    missing_asset_ids: tuple[str, ...]
    misplaced_asset_ids: tuple[str, ...]
    unexpected_values: tuple[str, ...]


class SerializedAssetAuditService:
    """Snapshot expected assets, record scans, and reconcile without auto-moving assets."""

    EXPECTED_STATUSES = {
        AssetLifecycle.RECEIVED.value,
        AssetLifecycle.IN_STOCK.value,
        AssetLifecycle.RETURNED.value,
        AssetLifecycle.REPAIR.value,
        AssetLifecycle.QUARANTINE.value,
        AssetLifecycle.RETIRED.value,
    }

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self.locations = LocationService(repository)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_asset_audits (
                    audit_id TEXT PRIMARY KEY,
                    location_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    opened_by TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_by TEXT NOT NULL DEFAULT '',
                    closed_at TEXT,
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS inventory_asset_audit_expected (
                    audit_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    expected_location TEXT NOT NULL,
                    expected_status TEXT NOT NULL,
                    PRIMARY KEY (audit_id, asset_id),
                    FOREIGN KEY (audit_id) REFERENCES inventory_asset_audits(audit_id)
                );
                CREATE TABLE IF NOT EXISTS inventory_asset_audit_scans (
                    scan_id TEXT PRIMARY KEY,
                    audit_id TEXT NOT NULL,
                    scanned_value TEXT NOT NULL,
                    asset_id TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL,
                    expected_location TEXT NOT NULL DEFAULT '',
                    actual_location TEXT NOT NULL DEFAULT '',
                    scanned_by TEXT NOT NULL,
                    scanned_at TEXT NOT NULL,
                    FOREIGN KEY (audit_id) REFERENCES inventory_asset_audits(audit_id)
                );
                CREATE INDEX IF NOT EXISTS idx_asset_audits_location_status
                    ON inventory_asset_audits(location_id, status, opened_at);
                CREATE INDEX IF NOT EXISTS idx_asset_audit_scans_audit
                    ON inventory_asset_audit_scans(audit_id, scanned_at);
                """
            )

    def open(self, *, location_id: str, actor: str, note: str = "") -> AssetAuditSession:
        location = self.locations.require_active(location_id)
        now = datetime.now(timezone.utc)
        audit_id = f"AUD-{uuid4().hex[:10]}"
        with self.repository.transaction() as conn:
            existing = conn.execute(
                "SELECT audit_id FROM inventory_asset_audits WHERE location_id=? AND status='open'",
                (location.location_id,),
            ).fetchone()
            if existing:
                raise InventoryDomainError(
                    f"Location {location.location_id} already has an open asset audit: {existing['audit_id']}"
                )
            conn.execute(
                """INSERT INTO inventory_asset_audits(
                    audit_id,location_id,status,opened_by,opened_at,note
                ) VALUES (?,?,'open',?,?,?)""",
                (audit_id, location.location_id, actor, now.isoformat(), note.strip()),
            )
            placeholders = ",".join("?" for _ in self.EXPECTED_STATUSES)
            rows = conn.execute(
                f"""SELECT asset_id,location_id,status FROM inventory_assets
                WHERE location_id=? AND status IN ({placeholders})
                ORDER BY asset_id""",
                (location.location_id, *sorted(self.EXPECTED_STATUSES)),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """INSERT INTO inventory_asset_audit_expected(
                        audit_id,asset_id,expected_location,expected_status
                    ) VALUES (?,?,?,?)""",
                    (audit_id, row["asset_id"], row["location_id"], row["status"]),
                )
        return self.require(audit_id)

    def scan(self, audit_id: str, *, value: str, actor: str) -> AssetAuditScan:
        audit = self.require_open(audit_id)
        token = value.strip()
        if not token:
            raise InventoryDomainError("Asset scan value is empty.")
        with self.repository.transaction() as conn:
            asset = conn.execute(
                "SELECT * FROM inventory_assets WHERE asset_id=? OR serial_number=? LIMIT 1",
                (token, token),
            ).fetchone()
            asset_id = asset["asset_id"] if asset else ""
            actual_location = asset["location_id"] if asset else ""
            expected = None
            if asset_id:
                expected = conn.execute(
                    "SELECT * FROM inventory_asset_audit_expected WHERE audit_id=? AND asset_id=?",
                    (audit.audit_id, asset_id),
                ).fetchone()
            if expected:
                result = "matched"
                expected_location = expected["expected_location"]
            elif asset:
                result = "misplaced"
                expected_location = actual_location
            else:
                result = "unknown"
                expected_location = ""
            existing = conn.execute(
                "SELECT scan_id FROM inventory_asset_audit_scans WHERE audit_id=? AND scanned_value=?",
                (audit.audit_id, token),
            ).fetchone()
            if existing:
                raise InventoryDomainError(f"Scan value already recorded in this audit: {token}")
            scan = AssetAuditScan(
                scan_id=f"SCAN-{uuid4().hex[:10]}",
                audit_id=audit.audit_id,
                scanned_value=token,
                asset_id=asset_id,
                result=result,
                expected_location=expected_location,
                actual_location=actual_location,
                scanned_by=actor,
                scanned_at=datetime.now(timezone.utc),
            )
            conn.execute(
                """INSERT INTO inventory_asset_audit_scans(
                    scan_id,audit_id,scanned_value,asset_id,result,expected_location,
                    actual_location,scanned_by,scanned_at
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    scan.scan_id,
                    scan.audit_id,
                    scan.scanned_value,
                    scan.asset_id,
                    scan.result,
                    scan.expected_location,
                    scan.actual_location,
                    scan.scanned_by,
                    scan.scanned_at.isoformat(),
                ),
            )
        return scan

    def close(self, audit_id: str, *, actor: str) -> AssetAuditResult:
        audit = self.require_open(audit_id)
        result = self.result(audit.audit_id)
        now = datetime.now(timezone.utc)
        with self.repository.transaction() as conn:
            conn.execute(
                """UPDATE inventory_asset_audits
                SET status='closed',closed_by=?,closed_at=? WHERE audit_id=?""",
                (actor, now.isoformat(), audit.audit_id),
            )
        return result

    def result(self, audit_id: str) -> AssetAuditResult:
        audit = self.require(audit_id)
        with self.repository._connect() as conn:
            expected = [
                row[0]
                for row in conn.execute(
                    "SELECT asset_id FROM inventory_asset_audit_expected WHERE audit_id=? ORDER BY asset_id",
                    (audit.audit_id,),
                ).fetchall()
            ]
            scans = conn.execute(
                "SELECT scanned_value,asset_id,result FROM inventory_asset_audit_scans WHERE audit_id=? ORDER BY scanned_at,scan_id",
                (audit.audit_id,),
            ).fetchall()
        matched = sorted({row["asset_id"] for row in scans if row["result"] == "matched" and row["asset_id"]})
        misplaced = sorted({row["asset_id"] for row in scans if row["result"] == "misplaced" and row["asset_id"]})
        unknown = tuple(row["scanned_value"] for row in scans if row["result"] == "unknown")
        missing = sorted(set(expected) - set(matched))
        return AssetAuditResult(
            audit_id=audit.audit_id,
            location_id=audit.location_id,
            expected_count=len(expected),
            matched_asset_ids=tuple(matched),
            missing_asset_ids=tuple(missing),
            misplaced_asset_ids=tuple(misplaced),
            unexpected_values=unknown,
        )

    def list_open(self) -> list[AssetAuditSession]:
        with self.repository._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_asset_audits WHERE status='open' ORDER BY opened_at,audit_id"
            ).fetchall()
        return [self._session(row) for row in rows]

    def scans(self, audit_id: str) -> list[AssetAuditScan]:
        self.require(audit_id)
        with self.repository._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_asset_audit_scans WHERE audit_id=? ORDER BY scanned_at,scan_id",
                (audit_id,),
            ).fetchall()
        return [self._scan(row) for row in rows]

    def require_open(self, audit_id: str) -> AssetAuditSession:
        audit = self.require(audit_id)
        if audit.status != "open":
            raise InventoryDomainError(f"Asset audit is not open: {audit_id}")
        return audit

    def require(self, audit_id: str) -> AssetAuditSession:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_asset_audits WHERE audit_id=?",
                (audit_id,),
            ).fetchone()
        if not row:
            raise InventoryDomainError(f"Unknown asset audit: {audit_id}")
        return self._session(row)

    @staticmethod
    def _session(row) -> AssetAuditSession:
        return AssetAuditSession(
            audit_id=row["audit_id"],
            location_id=row["location_id"],
            status=row["status"],
            opened_by=row["opened_by"],
            opened_at=datetime.fromisoformat(row["opened_at"]),
            closed_by=row["closed_by"],
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            note=row["note"],
        )

    @staticmethod
    def _scan(row) -> AssetAuditScan:
        return AssetAuditScan(
            scan_id=row["scan_id"],
            audit_id=row["audit_id"],
            scanned_value=row["scanned_value"],
            asset_id=row["asset_id"],
            result=row["result"],
            expected_location=row["expected_location"],
            actual_location=row["actual_location"],
            scanned_by=row["scanned_by"],
            scanned_at=datetime.fromisoformat(row["scanned_at"]),
        )

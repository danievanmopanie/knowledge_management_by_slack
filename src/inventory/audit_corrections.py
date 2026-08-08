"""Explicit corrective actions linked to serialized-asset audit findings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from src.inventory.audit_findings import AssetAuditFindingService
from src.inventory.domain import InventoryDomainError
from src.inventory.locations import LocationService
from src.inventory.repository import InventoryRepository


@dataclass(frozen=True)
class AssetAuditCorrection:
    correction_id: str
    finding_id: str
    action: str
    asset_id: str
    from_location: str
    to_location: str
    actor: str
    at: datetime
    note: str


class AssetAuditCorrectionService:
    """Apply narrow, explicit corrections after a physical-audit finding."""

    def __init__(self, repository: InventoryRepository):
        self.repository = repository
        self.findings = AssetAuditFindingService(repository)
        self.locations = LocationService(repository)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_asset_audit_corrections (
                    correction_id TEXT PRIMARY KEY,
                    finding_id TEXT NOT NULL UNIQUE,
                    action TEXT NOT NULL,
                    asset_id TEXT NOT NULL DEFAULT '',
                    from_location TEXT NOT NULL DEFAULT '',
                    to_location TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL,
                    at TEXT NOT NULL,
                    note TEXT NOT NULL,
                    FOREIGN KEY (finding_id)
                        REFERENCES inventory_asset_audit_findings(finding_id)
                );
                CREATE INDEX IF NOT EXISTS idx_asset_audit_corrections_asset
                    ON inventory_asset_audit_corrections(asset_id, at);
                """
            )

    def relocate(self, finding_id: str, *, actor: str, note: str, to_location: str = "") -> AssetAuditCorrection:
        finding = self.findings.require(finding_id)
        self._require_open(finding.status, finding_id)
        if finding.finding_type != "misplaced" or not finding.asset_id:
            raise InventoryDomainError("Relocation correction requires an open misplaced-asset finding.")
        destination_id = (to_location or finding.observed_location).strip()
        if not destination_id:
            raise InventoryDomainError("Relocation correction requires a destination location.")
        destination = self.locations.require_active(destination_id)
        asset = self.repository.load_asset(finding.asset_id)
        if asset is None:
            raise InventoryDomainError(f"Unknown serialized asset: {finding.asset_id}")
        if not note.strip():
            raise InventoryDomainError("Audit correction requires a note.")
        correction = AssetAuditCorrection(
            correction_id=f"COR-{uuid4().hex[:10]}",
            finding_id=finding.finding_id,
            action="relocate",
            asset_id=asset.asset_id,
            from_location=asset.location_id,
            to_location=destination.location_id,
            actor=actor,
            at=datetime.now(timezone.utc),
            note=note.strip(),
        )
        with self.repository.transaction() as conn:
            self._ensure_no_correction(conn, finding.finding_id)
            metadata = dict(asset.metadata)
            metadata["last_audit_correction"] = correction.correction_id
            metadata["last_audit_finding"] = finding.finding_id
            conn.execute(
                "UPDATE inventory_assets SET location_id=?, metadata_json=? WHERE asset_id=?",
                (destination.location_id, json.dumps(metadata), asset.asset_id),
            )
            self._insert(conn, correction)
            self._resolve_finding(
                conn,
                finding_id=finding.finding_id,
                resolution_type="relocated",
                actor=actor,
                note=f"{note.strip()} | correction {correction.correction_id}",
                at=correction.at,
            )
        return correction

    def start_lost_investigation(self, finding_id: str, *, actor: str, note: str) -> AssetAuditCorrection:
        finding = self.findings.require(finding_id)
        self._require_open(finding.status, finding_id)
        if finding.finding_type != "missing" or not finding.asset_id:
            raise InventoryDomainError("Lost-investigation correction requires an open missing-asset finding.")
        if not note.strip():
            raise InventoryDomainError("Audit correction requires a note.")
        asset = self.repository.load_asset(finding.asset_id)
        if asset is None:
            raise InventoryDomainError(f"Unknown serialized asset: {finding.asset_id}")
        correction = AssetAuditCorrection(
            correction_id=f"COR-{uuid4().hex[:10]}",
            finding_id=finding.finding_id,
            action="lost_investigation",
            asset_id=asset.asset_id,
            from_location=asset.location_id,
            to_location=asset.location_id,
            actor=actor,
            at=datetime.now(timezone.utc),
            note=note.strip(),
        )
        with self.repository.transaction() as conn:
            self._ensure_no_correction(conn, finding.finding_id)
            metadata = dict(asset.metadata)
            metadata["audit_investigation_status"] = "lost_investigation"
            metadata["audit_investigation_finding"] = finding.finding_id
            metadata["audit_investigation_note"] = note.strip()
            conn.execute(
                "UPDATE inventory_assets SET metadata_json=? WHERE asset_id=?",
                (json.dumps(metadata), asset.asset_id),
            )
            self._insert(conn, correction)
            self._resolve_finding(
                conn,
                finding_id=finding.finding_id,
                resolution_type="lost_investigation",
                actor=actor,
                note=f"{note.strip()} | correction {correction.correction_id}",
                at=correction.at,
            )
        return correction

    def dismiss_unknown_scan(self, finding_id: str, *, actor: str, note: str) -> AssetAuditCorrection:
        finding = self.findings.require(finding_id)
        self._require_open(finding.status, finding_id)
        if finding.finding_type != "unknown":
            raise InventoryDomainError("Unknown-scan dismissal requires an open unknown finding.")
        if not note.strip():
            raise InventoryDomainError("Audit correction requires a note.")
        correction = AssetAuditCorrection(
            correction_id=f"COR-{uuid4().hex[:10]}",
            finding_id=finding.finding_id,
            action="false_scan",
            asset_id="",
            from_location="",
            to_location="",
            actor=actor,
            at=datetime.now(timezone.utc),
            note=note.strip(),
        )
        with self.repository.transaction() as conn:
            self._ensure_no_correction(conn, finding.finding_id)
            self._insert(conn, correction)
            self._resolve_finding(
                conn,
                finding_id=finding.finding_id,
                resolution_type="false_scan",
                actor=actor,
                note=f"{note.strip()} | correction {correction.correction_id}",
                at=correction.at,
            )
        return correction

    def list(self, *, asset_id: str = "") -> list[AssetAuditCorrection]:
        sql = "SELECT * FROM inventory_asset_audit_corrections"
        params: tuple[str, ...] = ()
        if asset_id:
            sql += " WHERE asset_id=?"
            params = (asset_id,)
        sql += " ORDER BY at, correction_id"
        with self.repository._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row(row) for row in rows]

    def require(self, correction_id: str) -> AssetAuditCorrection:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_asset_audit_corrections WHERE correction_id=?",
                (correction_id,),
            ).fetchone()
        if not row:
            raise InventoryDomainError(f"Unknown asset audit correction: {correction_id}")
        return self._row(row)

    @staticmethod
    def _require_open(status: str, finding_id: str) -> None:
        if status != "open":
            raise InventoryDomainError(f"Audit finding is not open: {finding_id}")

    @staticmethod
    def _ensure_no_correction(conn, finding_id: str) -> None:
        row = conn.execute(
            "SELECT correction_id FROM inventory_asset_audit_corrections WHERE finding_id=?",
            (finding_id,),
        ).fetchone()
        if row:
            raise InventoryDomainError(
                f"Audit finding already has correction {row['correction_id']}: {finding_id}"
            )

    @staticmethod
    def _insert(conn, correction: AssetAuditCorrection) -> None:
        conn.execute(
            """INSERT INTO inventory_asset_audit_corrections(
                correction_id,finding_id,action,asset_id,from_location,to_location,
                actor,at,note
            ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                correction.correction_id,
                correction.finding_id,
                correction.action,
                correction.asset_id,
                correction.from_location,
                correction.to_location,
                correction.actor,
                correction.at.isoformat(),
                correction.note,
            ),
        )

    @staticmethod
    def _resolve_finding(conn, *, finding_id: str, resolution_type: str, actor: str, note: str, at: datetime) -> None:
        conn.execute(
            """UPDATE inventory_asset_audit_findings
            SET status='resolved',resolution_type=?,resolution_note=?,resolved_by=?,resolved_at=?
            WHERE finding_id=? AND status='open'""",
            (resolution_type, note, actor, at.isoformat(), finding_id),
        )
        conn.execute(
            """INSERT INTO inventory_asset_audit_finding_events(
                event_id,finding_id,action,actor,note,at
            ) VALUES (?,?,?,?,?,?)""",
            (str(uuid4()), finding_id, "corrected", actor, note, at.isoformat()),
        )

    @staticmethod
    def _row(row) -> AssetAuditCorrection:
        return AssetAuditCorrection(
            correction_id=row["correction_id"],
            finding_id=row["finding_id"],
            action=row["action"],
            asset_id=row["asset_id"],
            from_location=row["from_location"],
            to_location=row["to_location"],
            actor=row["actor"],
            at=datetime.fromisoformat(row["at"]),
            note=row["note"],
        )

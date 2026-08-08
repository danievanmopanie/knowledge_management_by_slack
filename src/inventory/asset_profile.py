"""Lifecycle profile, aging and disposal-evidence services for serialized assets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from src.inventory.domain import AssetLifecycle, InventoryDomainError
from src.inventory.repository import InventoryRepository


@dataclass(frozen=True)
class AssetLifecycleProfile:
    asset_id: str
    sku: str
    serial_number: str
    status: AssetLifecycle
    received_at: datetime | None
    warranty_end: datetime | None
    age_days: int | None
    age_years: float | None
    warranty_status: str
    retirement_eligible: bool
    retirement_age_years: float
    retired_at: datetime | None
    disposed_at: datetime | None


@dataclass(frozen=True)
class DisposalEvidence:
    evidence_id: str
    asset_id: str
    evidence_type: str
    reference: str
    note: str
    recorded_by: str
    recorded_at: datetime


class AssetLifecycleProfileService:
    """Derived asset lifecycle views and durable disposal evidence."""

    ALLOWED_EVIDENCE_TYPES = {
        "certificate",
        "collection_note",
        "recycler_receipt",
        "destruction_record",
        "handover",
        "other",
    }

    def __init__(self, repository: InventoryRepository, retirement_age_years: float = 4.0):
        if retirement_age_years <= 0:
            raise InventoryDomainError("Retirement age must be positive.")
        self.repository = repository
        self.retirement_age_years = float(retirement_age_years)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_disposal_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    reference TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    recorded_by TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (asset_id) REFERENCES inventory_assets(asset_id)
                );
                CREATE INDEX IF NOT EXISTS idx_inventory_disposal_evidence_asset
                    ON inventory_disposal_evidence(asset_id, recorded_at);
                """
            )

    def profile(self, asset_id: str, *, at: datetime | None = None) -> AssetLifecycleProfile:
        asset = self.repository.load_asset(asset_id)
        if asset is None:
            raise InventoryDomainError(f"Unknown serialized asset: {asset_id}")
        now = at or datetime.now(timezone.utc)
        age_days: int | None = None
        age_years: float | None = None
        if asset.received_at:
            received = self._aware(asset.received_at)
            age_days = max(0, (now - received).days)
            age_years = age_days / 365.25
        warranty_status = "unknown"
        if asset.warranty_end:
            warranty_status = "in_warranty" if self._aware(asset.warranty_end) >= now else "expired"
        eligible = bool(age_years is not None and age_years >= self.retirement_age_years)
        return AssetLifecycleProfile(
            asset_id=asset.asset_id,
            sku=asset.sku,
            serial_number=asset.serial_number,
            status=asset.status,
            received_at=asset.received_at,
            warranty_end=asset.warranty_end,
            age_days=age_days,
            age_years=age_years,
            warranty_status=warranty_status,
            retirement_eligible=eligible,
            retirement_age_years=self.retirement_age_years,
            retired_at=asset.retired_at,
            disposed_at=asset.disposed_at,
        )

    def set_dates(
        self,
        asset_id: str,
        *,
        received_at: datetime | None = None,
        warranty_end: datetime | None = None,
    ) -> AssetLifecycleProfile:
        if received_at is None and warranty_end is None:
            raise InventoryDomainError("At least one lifecycle date must be supplied.")
        with self.repository.transaction() as conn:
            asset = self.repository.load_asset(asset_id, conn)
            if asset is None:
                raise InventoryDomainError(f"Unknown serialized asset: {asset_id}")
            received = received_at if received_at is not None else asset.received_at
            warranty = warranty_end if warranty_end is not None else asset.warranty_end
            if received and warranty and self._aware(warranty) < self._aware(received):
                raise InventoryDomainError("Warranty end cannot be before the received date.")
            conn.execute(
                "UPDATE inventory_assets SET received_at=?, warranty_end=? WHERE asset_id=?",
                (
                    received.isoformat() if received else None,
                    warranty.isoformat() if warranty else None,
                    asset.asset_id,
                ),
            )
        return self.profile(asset_id)

    def out_of_warranty(self, *, at: datetime | None = None) -> list[AssetLifecycleProfile]:
        return [item for item in self._profiles(at=at) if item.warranty_status == "expired" and item.status != AssetLifecycle.DISPOSED]

    def older_than(self, years: float, *, at: datetime | None = None) -> list[AssetLifecycleProfile]:
        if years < 0:
            raise InventoryDomainError("Age threshold cannot be negative.")
        return [item for item in self._profiles(at=at) if item.age_years is not None and item.age_years >= years and item.status != AssetLifecycle.DISPOSED]

    def retirement_candidates(self, *, at: datetime | None = None) -> list[AssetLifecycleProfile]:
        return [
            item for item in self._profiles(at=at)
            if item.retirement_eligible and item.status not in {AssetLifecycle.RETIRED, AssetLifecycle.DISPOSED}
        ]

    def awaiting_disposal(self) -> list[AssetLifecycleProfile]:
        return [item for item in self._profiles() if item.status == AssetLifecycle.RETIRED]

    def add_disposal_evidence(
        self,
        asset_id: str,
        *,
        evidence_type: str,
        actor: str,
        reference: str = "",
        note: str = "",
    ) -> DisposalEvidence:
        evidence_type = evidence_type.strip().lower()
        if evidence_type not in self.ALLOWED_EVIDENCE_TYPES:
            allowed = ", ".join(sorted(self.ALLOWED_EVIDENCE_TYPES))
            raise InventoryDomainError(f"Disposal evidence type must be one of: {allowed}.")
        asset = self.repository.load_asset(asset_id)
        if asset is None:
            raise InventoryDomainError(f"Unknown serialized asset: {asset_id}")
        if asset.status not in {AssetLifecycle.RETIRED, AssetLifecycle.DISPOSED}:
            raise InventoryDomainError("Disposal evidence can only be recorded for retired or disposed assets.")
        if not reference.strip() and not note.strip():
            raise InventoryDomainError("Disposal evidence requires a reference or note.")
        evidence = DisposalEvidence(
            evidence_id=f"DSP-{uuid4().hex[:10]}",
            asset_id=asset.asset_id,
            evidence_type=evidence_type,
            reference=reference.strip(),
            note=note.strip(),
            recorded_by=actor,
            recorded_at=datetime.now(timezone.utc),
        )
        with self.repository.transaction() as conn:
            conn.execute(
                """INSERT INTO inventory_disposal_evidence(
                    evidence_id,asset_id,evidence_type,reference,note,recorded_by,recorded_at
                ) VALUES (?,?,?,?,?,?,?)""",
                (
                    evidence.evidence_id,
                    evidence.asset_id,
                    evidence.evidence_type,
                    evidence.reference,
                    evidence.note,
                    evidence.recorded_by,
                    evidence.recorded_at.isoformat(),
                ),
            )
        return evidence

    def disposal_evidence(self, asset_id: str) -> list[DisposalEvidence]:
        if self.repository.load_asset(asset_id) is None:
            raise InventoryDomainError(f"Unknown serialized asset: {asset_id}")
        with self.repository._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_disposal_evidence WHERE asset_id=? ORDER BY recorded_at, evidence_id",
                (asset_id,),
            ).fetchall()
        return [
            DisposalEvidence(
                evidence_id=row["evidence_id"],
                asset_id=row["asset_id"],
                evidence_type=row["evidence_type"],
                reference=row["reference"],
                note=row["note"],
                recorded_by=row["recorded_by"],
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
            )
            for row in rows
        ]

    def can_dispose(self, asset_id: str) -> bool:
        asset = self.repository.load_asset(asset_id)
        if asset is None:
            raise InventoryDomainError(f"Unknown serialized asset: {asset_id}")
        return asset.status == AssetLifecycle.RETIRED and bool(self.disposal_evidence(asset_id))

    def _profiles(self, *, at: datetime | None = None) -> list[AssetLifecycleProfile]:
        with self.repository._connect() as conn:
            ids = [row[0] for row in conn.execute("SELECT asset_id FROM inventory_assets ORDER BY asset_id").fetchall()]
        return [self.profile(asset_id, at=at) for asset_id in ids]

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

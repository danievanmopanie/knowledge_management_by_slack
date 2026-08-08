from datetime import datetime, timezone

import pytest

from src.inventory.asset_profile import AssetLifecycleProfileService
from src.inventory.domain import AssetLifecycle, InventoryDomainError, SerializedAsset
from src.inventory.repository import InventoryRepository


def seed_asset(
    repo,
    *,
    asset_id="A-1",
    status=AssetLifecycle.IN_STOCK,
    received_at=None,
    warranty_end=None,
):
    with repo.transaction() as conn:
        repo.save_asset(
            SerializedAsset(
                asset_id=asset_id,
                sku="LAPTOP-01",
                serial_number=f"SER-{asset_id}",
                status=status,
                location_id="STORE-A",
                purchase_order_id="PO-1",
                received_at=received_at,
                warranty_end=warranty_end,
            ),
            conn,
        )


def test_profile_derives_age_warranty_and_retirement_eligibility(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    received = datetime(2021, 8, 1, tzinfo=timezone.utc)
    warranty = datetime(2024, 8, 1, tzinfo=timezone.utc)
    seed_asset(repo, received_at=received, warranty_end=warranty)
    service = AssetLifecycleProfileService(repo, retirement_age_years=4)

    profile = service.profile("A-1", at=datetime(2026, 8, 8, tzinfo=timezone.utc))

    assert profile.age_years is not None
    assert profile.age_years > 5
    assert profile.warranty_status == "expired"
    assert profile.retirement_eligible is True


def test_set_dates_rejects_warranty_before_received_date(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo)
    service = AssetLifecycleProfileService(repo)

    with pytest.raises(InventoryDomainError, match="Warranty end cannot be before"):
        service.set_dates(
            "A-1",
            received_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            warranty_end=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )


def test_out_of_warranty_and_age_reports_exclude_disposed_assets(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    expired = datetime(2023, 1, 1, tzinfo=timezone.utc)
    seed_asset(repo, asset_id="ACTIVE", received_at=old, warranty_end=expired)
    seed_asset(
        repo,
        asset_id="DISPOSED",
        status=AssetLifecycle.DISPOSED,
        received_at=old,
        warranty_end=expired,
    )
    service = AssetLifecycleProfileService(repo)
    at = datetime(2026, 8, 8, tzinfo=timezone.utc)

    assert [item.asset_id for item in service.out_of_warranty(at=at)] == ["ACTIVE"]
    assert [item.asset_id for item in service.older_than(4, at=at)] == ["ACTIVE"]
    assert [item.asset_id for item in service.retirement_candidates(at=at)] == ["ACTIVE"]


def test_retired_assets_require_disposal_evidence_and_keep_history(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo, status=AssetLifecycle.RETIRED)
    service = AssetLifecycleProfileService(repo)

    assert [item.asset_id for item in service.awaiting_disposal()] == ["A-1"]
    assert service.can_dispose("A-1") is False

    evidence = service.add_disposal_evidence(
        "A-1",
        evidence_type="certificate",
        reference="CERT-2026-77",
        note="Certified destruction",
        actor="U1",
    )

    assert evidence.evidence_id.startswith("DSP-")
    assert service.can_dispose("A-1") is True
    history = service.disposal_evidence("A-1")
    assert len(history) == 1
    assert history[0].reference == "CERT-2026-77"


def test_disposal_evidence_cannot_be_added_to_active_asset(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_asset(repo, status=AssetLifecycle.IN_STOCK)
    service = AssetLifecycleProfileService(repo)

    with pytest.raises(InventoryDomainError, match="retired or disposed"):
        service.add_disposal_evidence(
            "A-1",
            evidence_type="handover",
            reference="H-1",
            actor="U1",
        )

from datetime import datetime, timedelta, timezone

from src.inventory.asset_lifecycle import SerializedAssetLifecycleService
from src.inventory.assisted_receiving import AssistedReceivingWorkflow
from src.inventory.domain import AllocationType, AssetLifecycle, SerializedAsset, StockTransaction
from src.inventory.exceptions import InventoryExceptionService
from src.inventory.locations import LocationService
from src.inventory.overview import InventoryOverviewService
from src.inventory.po_intake import PurchaseOrderIntakeWorkflow
from src.inventory.procurement import (
    DiscrepancyType,
    PurchaseOrder,
    PurchaseOrderLine,
    ReconciliationDiscrepancy,
)
from src.inventory.quantity_stock import QuantityStockService
from src.inventory.repository import InventoryRepository
from src.inventory.stock_reconciliation import StockReconciliationWorkflow
from src.inventory.domain import TrackingMode


def test_overview_aggregates_operational_attention_items(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

    locations = LocationService(repo)
    locations.create(
        location_id="STORE-A",
        name="Main Store",
        site="SITE-A",
        location_type="storeroom",
        actor="admin",
    )

    po = PurchaseOrder(
        purchase_order_id="PO-1",
        supplier="Supplier",
        created_by="U1",
        lines=[
            PurchaseOrderLine(
                line_id="10",
                sku="MOUSE-01",
                description="Mouse",
                quantity_ordered=10,
                tracking_mode=TrackingMode.QUANTITY,
            )
        ],
    )
    repo.save_purchase_order(po)

    with repo.transaction() as conn:
        repo.post_stock_transaction(
            StockTransaction(
                transaction_id="T-1",
                sku="MOUSE-01",
                quantity=10,
                transaction_type="receive",
                actor="U1",
                to_location="STORE-A",
            ),
            conn,
        )
        repo.save_asset(
            SerializedAsset(
                asset_id="A-1",
                sku="LAPTOP-01",
                serial_number="SER-1",
                status=AssetLifecycle.IN_STOCK,
                location_id="STORE-A",
            ),
            conn,
        )

    lifecycle = SerializedAssetLifecycleService(repo)
    lifecycle.issue(
        "A-1",
        assigned_to="U123",
        customer_ref="EMP-42",
        allocation_type=AllocationType.LOAN,
        return_due_at=now - timedelta(days=3),
        actor="U1",
    )

    stock = QuantityStockService(repo)
    stock.reserve(
        sku="MOUSE-01",
        location_id="STORE-A",
        quantity=2,
        customer_ref="EMP-42",
        requested_by="U1",
    )
    stock.set_reorder_rule(
        sku="MOUSE-01",
        location_id="STORE-A",
        reorder_point=8,
        reorder_quantity=20,
    )

    InventoryExceptionService(repo)
    with repo.transaction() as conn:
        repo.save_discrepancy(
            "PO-1",
            "DEL-1",
            ReconciliationDiscrepancy(
                discrepancy_type=DiscrepancyType.SHORT,
                delivery_line_id="DL-1",
                po_line_id="10",
                sku="MOUSE-01",
                expected_quantity=10,
                actual_quantity=8,
                detail="Short by two.",
            ),
            conn,
        )

    PurchaseOrderIntakeWorkflow(repo)
    AssistedReceivingWorkflow(repo)
    counts = StockReconciliationWorkflow(repo)
    counts.stage(
        sku="MOUSE-01",
        location_id="STORE-A",
        counted_quantity=10,
        actor="U1",
    )

    overview = InventoryOverviewService(repo).snapshot(now=now)

    assert overview.active_locations == 1
    assert overview.open_purchase_orders == 1
    assert overview.serialized_assets == 1
    assert overview.issued_assets == 1
    assert overview.overdue_loans == 1
    assert overview.stock_units_on_hand == 10
    assert overview.stock_units_reserved == 2
    assert overview.low_stock_positions == 1
    assert overview.open_exceptions == 1
    assert overview.pending_stock_counts == 1


def test_overdue_loans_are_sorted_oldest_first(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    with repo.transaction() as conn:
        for asset_id, days in (("A-OLD", 10), ("A-NEW", 2)):
            repo.save_asset(
                SerializedAsset(
                    asset_id=asset_id,
                    sku="LAPTOP-01",
                    serial_number=f"SER-{asset_id}",
                    status=AssetLifecycle.ISSUED,
                    assigned_to="U1",
                    customer_ref="EMP-1",
                    allocation_type=AllocationType.LOAN,
                    return_due_at=now - timedelta(days=days),
                ),
                conn,
            )

    alerts = InventoryOverviewService(repo).overdue_loans(now=now)

    assert [item.asset_id for item in alerts] == ["A-OLD", "A-NEW"]
    assert alerts[0].days_overdue == 10

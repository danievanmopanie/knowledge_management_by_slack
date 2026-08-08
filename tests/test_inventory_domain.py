from src.inventory.domain import (
    CatalogItem,
    InventoryDomainError,
    ReconciliationCount,
    StockLedger,
    StockTransaction,
    TrackingMode,
)


def tx(transaction_id, sku, quantity, kind, *, source="", destination=""):
    return StockTransaction(
        transaction_id=transaction_id,
        sku=sku,
        quantity=quantity,
        transaction_type=kind,
        actor="tester",
        from_location=source,
        to_location=destination,
    )


def test_receive_issue_return_and_transfer():
    ledger = StockLedger()
    ledger.apply(tx("T1", "MOUSE-01", 10, "receive", destination="STORE-A"))
    ledger.apply(tx("T2", "MOUSE-01", 3, "issue", source="STORE-A"))
    ledger.apply(tx("T3", "MOUSE-01", 1, "return", destination="STORE-A"))
    ledger.apply(tx("T4", "MOUSE-01", 2, "transfer", source="STORE-A", destination="STORE-B"))

    assert ledger.balance("MOUSE-01", "STORE-A").on_hand == 6
    assert ledger.balance("MOUSE-01", "STORE-B").on_hand == 2


def test_cannot_issue_more_than_available():
    ledger = StockLedger()
    ledger.apply(tx("T1", "DOCK-01", 2, "receive", destination="STORE-A"))

    try:
        ledger.apply(tx("T2", "DOCK-01", 3, "issue", source="STORE-A"))
    except InventoryDomainError as exc:
        assert "Insufficient available stock" in str(exc)
    else:
        raise AssertionError("Expected InventoryDomainError")


def test_reconciliation_records_variance_in_balance():
    ledger = StockLedger()
    ledger.apply(tx("T1", "HEADSET-01", 10, "receive", destination="STORE-A"))

    variance = ledger.reconcile(
        ReconciliationCount(
            sku="HEADSET-01",
            location_id="STORE-A",
            expected_quantity=10,
            counted_quantity=8,
        )
    )

    assert variance == -2
    assert ledger.balance("HEADSET-01", "STORE-A").on_hand == 8


def test_reorder_point_uses_available_stock():
    ledger = StockLedger()
    item = CatalogItem(
        sku="KB-01",
        name="USB keyboard",
        category="Peripheral",
        tracking_mode=TrackingMode.QUANTITY,
        reorder_point=5,
        reorder_quantity=20,
    )
    ledger.apply(tx("T1", item.sku, 5, "receive", destination="STORE-A"))

    assert ledger.below_reorder_point(item, "STORE-A") is True

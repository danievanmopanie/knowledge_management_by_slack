from src.inventory.domain import StockTransaction
from src.inventory.quantity_stock import QuantityStockService
from src.inventory.repository import InventoryRepository
from src.inventory.stock_reconciliation import StockReconciliationWorkflow


def seed_stock(repo: InventoryRepository, quantity: int = 10) -> None:
    with repo.transaction() as conn:
        repo.post_stock_transaction(
            StockTransaction(
                transaction_id="seed-stock",
                sku="MOUSE-01",
                quantity=quantity,
                transaction_type="receive",
                actor="seed",
                to_location="STORE-A",
            ),
            conn,
        )


def test_stage_does_not_change_stock_until_confirmed(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_stock(repo)
    workflow = StockReconciliationWorkflow(repo)

    staged = workflow.stage(
        sku="MOUSE-01",
        location_id="STORE-A",
        counted_quantity=8,
        actor="U1",
    )

    assert staged.variance == -2
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 10

    confirmed = workflow.confirm(staged.count_id, actor="U1")

    assert confirmed.status == "confirmed"
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 8


def test_cancel_leaves_ledger_unchanged(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_stock(repo)
    workflow = StockReconciliationWorkflow(repo)

    staged = workflow.stage(
        sku="MOUSE-01",
        location_id="STORE-A",
        counted_quantity=7,
        actor="U1",
    )
    cancelled = workflow.cancel(staged.count_id, actor="U1")

    assert cancelled.status == "cancelled"
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 10


def test_stale_count_is_rejected_if_stock_changed_after_staging(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_stock(repo)
    workflow = StockReconciliationWorkflow(repo)
    stock = QuantityStockService(repo)

    staged = workflow.stage(
        sku="MOUSE-01",
        location_id="STORE-A",
        counted_quantity=8,
        actor="U1",
    )
    stock.return_stock(
        sku="MOUSE-01",
        location_id="STORE-A",
        quantity=1,
        customer_ref="EMP-42",
        actor="U2",
    )

    try:
        workflow.confirm(staged.count_id, actor="U1")
    except Exception as exc:
        assert "Stock changed" in str(exc)
    else:
        raise AssertionError("Expected stale stock-count failure")

    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 11


def test_count_below_reserved_stock_cannot_be_confirmed(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_stock(repo, 5)
    workflow = StockReconciliationWorkflow(repo)
    stock = QuantityStockService(repo)
    stock.reserve(
        sku="MOUSE-01",
        location_id="STORE-A",
        quantity=4,
        customer_ref="EMP-42",
        requested_by="U1",
    )

    staged = workflow.stage(
        sku="MOUSE-01",
        location_id="STORE-A",
        counted_quantity=3,
        actor="U1",
    )

    try:
        workflow.confirm(staged.count_id, actor="U1")
    except Exception as exc:
        assert "below the currently reserved quantity" in str(exc)
    else:
        raise AssertionError("Expected reserved-stock protection failure")

    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 5


def test_only_staging_user_can_confirm(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_stock(repo)
    workflow = StockReconciliationWorkflow(repo)
    staged = workflow.stage(
        sku="MOUSE-01",
        location_id="STORE-A",
        counted_quantity=10,
        actor="U1",
    )

    try:
        workflow.confirm(staged.count_id, actor="U2")
    except Exception as exc:
        assert "Only the user" in str(exc)
    else:
        raise AssertionError("Expected actor-ownership failure")

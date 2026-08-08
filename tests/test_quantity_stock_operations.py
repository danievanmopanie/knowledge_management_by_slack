import sqlite3

import pytest

from src.inventory.domain import InventoryDomainError, StockTransaction
from src.inventory.quantity_stock import QuantityStockService
from src.inventory.repository import InventoryRepository


def seed_stock(repo, *, sku="MOUSE-01", location="STORE-A", quantity=10):
    with repo.transaction() as conn:
        repo.post_stock_transaction(
            StockTransaction(
                transaction_id=f"seed-{sku}-{location}",
                sku=sku,
                quantity=quantity,
                transaction_type="receive",
                actor="seed",
                to_location=location,
            ),
            conn,
        )


def seed_customer(service, customer_id="EMP-1"):
    service.customers.create(
        customer_id=customer_id,
        name=f"Customer {customer_id}",
        customer_type="employee",
        actor="admin",
    )


def test_reserve_and_issue_reserved_stock(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = QuantityStockService(repo)
    seed_stock(repo)
    seed_customer(service)

    reservation = service.reserve(
        sku="MOUSE-01",
        location_id="STORE-A",
        quantity=4,
        customer_ref="EMP-1",
        requested_by="U1",
    )
    assert service.available("MOUSE-01", "STORE-A") == 6

    service.issue(
        sku="MOUSE-01",
        location_id="STORE-A",
        quantity=4,
        customer_ref="EMP-1",
        actor="U1",
        reservation_id=reservation.reservation_id,
    )

    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 6
    assert service.available("MOUSE-01", "STORE-A") == 6


def test_reservation_blocks_unreserved_issue(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = QuantityStockService(repo)
    seed_stock(repo, quantity=5)
    seed_customer(service, "EMP-1")
    seed_customer(service, "EMP-2")
    service.reserve(
        sku="MOUSE-01",
        location_id="STORE-A",
        quantity=4,
        customer_ref="EMP-1",
        requested_by="U1",
    )

    with pytest.raises(InventoryDomainError, match="Insufficient available stock"):
        service.issue(
            sku="MOUSE-01",
            location_id="STORE-A",
            quantity=2,
            customer_ref="EMP-2",
            actor="U2",
        )


def test_reserved_issue_must_match_customer(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = QuantityStockService(repo)
    seed_stock(repo, quantity=5)
    seed_customer(service, "EMP-1")
    seed_customer(service, "EMP-2")
    reservation = service.reserve(
        sku="MOUSE-01",
        location_id="STORE-A",
        quantity=2,
        customer_ref="EMP-1",
        requested_by="U1",
    )

    with pytest.raises(InventoryDomainError, match="different customer"):
        service.issue(
            sku="MOUSE-01",
            location_id="STORE-A",
            quantity=2,
            customer_ref="EMP-2",
            actor="U2",
            reservation_id=reservation.reservation_id,
        )


def test_return_and_transfer_are_persisted(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = QuantityStockService(repo)
    seed_stock(repo, quantity=10)
    seed_customer(service)

    service.issue(
        sku="MOUSE-01",
        location_id="STORE-A",
        quantity=3,
        customer_ref="EMP-1",
        actor="U1",
    )
    service.return_stock(
        sku="MOUSE-01",
        location_id="STORE-A",
        quantity=1,
        customer_ref="EMP-1",
        actor="U1",
    )
    service.transfer(
        sku="MOUSE-01",
        from_location="STORE-A",
        to_location="STORE-B",
        quantity=2,
        actor="U1",
    )

    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 6
    assert repo.stock_on_hand("MOUSE-01", "STORE-B") == 2


def test_count_reconciliation_and_low_stock(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = QuantityStockService(repo)
    seed_stock(repo, quantity=10)
    service.set_reorder_rule(
        sku="MOUSE-01",
        location_id="STORE-A",
        reorder_point=8,
        reorder_quantity=20,
    )

    result = service.count_and_reconcile(
        sku="MOUSE-01",
        location_id="STORE-A",
        counted_quantity=7,
        actor="U1",
        note="Cycle count",
    )

    assert result.variance == -3
    assert repo.stock_on_hand("MOUSE-01", "STORE-A") == 7
    low = service.low_stock()
    assert len(low) == 1
    assert low[0]["sku"] == "MOUSE-01"
    assert low[0]["reorder_quantity"] == 20


def test_reservation_and_balance_update_are_atomic(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    service = QuantityStockService(repo)
    seed_stock(repo, quantity=10)
    seed_customer(service)

    with repo.transaction() as conn:
        conn.execute(
            "CREATE TRIGGER fail_reservation BEFORE INSERT ON inventory_stock_reservations "
            "BEGIN SELECT RAISE(ABORT, 'reservation failure'); END;"
        )

    with pytest.raises(sqlite3.IntegrityError, match="reservation failure"):
        service.reserve(
            sku="MOUSE-01",
            location_id="STORE-A",
            quantity=4,
            customer_ref="EMP-1",
            requested_by="U1",
        )

    assert service.available("MOUSE-01", "STORE-A") == 10

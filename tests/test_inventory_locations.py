from src.inventory.domain import InventoryDomainError, StockTransaction
from src.inventory.locations import LocationService
from src.inventory.repository import InventoryRepository


def test_location_hierarchy_requires_known_parent_and_same_site(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    locations = LocationService(repo)
    locations.create(
        location_id="SITE-A",
        name="Site A",
        site="SITE-A",
        location_type="site",
        actor="admin",
    )
    locations.create(
        location_id="STORE-A",
        name="Main Store",
        site="SITE-A",
        location_type="storeroom",
        parent_id="SITE-A",
        actor="admin",
    )

    assert [item.location_id for item in locations.path("STORE-A")] == ["SITE-A", "STORE-A"]

    try:
        locations.create(
            location_id="BAD-BIN",
            name="Bad Bin",
            site="SITE-B",
            location_type="bin",
            parent_id="STORE-A",
            actor="admin",
        )
    except InventoryDomainError as exc:
        assert "same site" in str(exc)
    else:
        raise AssertionError("Expected cross-site hierarchy failure")


def test_location_with_stock_cannot_be_deactivated(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    locations = LocationService(repo)
    locations.create(
        location_id="STORE-A",
        name="Main Store",
        site="SITE-A",
        location_type="storeroom",
        actor="admin",
    )
    with repo.transaction() as conn:
        repo.post_stock_transaction(
            StockTransaction(
                transaction_id="T-1",
                sku="MOUSE-01",
                quantity=5,
                transaction_type="receive",
                actor="admin",
                to_location="STORE-A",
            ),
            conn,
        )

    try:
        locations.set_active("STORE-A", active=False)
    except InventoryDomainError as exc:
        assert "still contains inventory" in str(exc)
    else:
        raise AssertionError("Expected occupied-location failure")


def test_parent_with_active_child_cannot_be_deactivated(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    locations = LocationService(repo)
    locations.create(
        location_id="SITE-A",
        name="Site A",
        site="SITE-A",
        location_type="site",
        actor="admin",
    )
    locations.create(
        location_id="STORE-A",
        name="Main Store",
        site="SITE-A",
        location_type="storeroom",
        parent_id="SITE-A",
        actor="admin",
    )

    try:
        locations.set_active("SITE-A", active=False)
    except InventoryDomainError as exc:
        assert "child" in str(exc)
    else:
        raise AssertionError("Expected active-child failure")

    locations.set_active("STORE-A", active=False)
    locations.set_active("SITE-A", active=False)
    assert locations.get("SITE-A").active is False

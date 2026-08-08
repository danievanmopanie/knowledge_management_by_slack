import pytest

from src.inventory.domain import InventoryDomainError, StockTransaction, TrackingMode
from src.inventory.items import ItemCatalogService
from src.inventory.repository import InventoryRepository


def test_item_catalog_create_list_and_reorder_defaults(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    items = ItemCatalogService(repo)

    item = items.create(
        sku="mouse-01",
        name="USB Mouse",
        tracking_mode="quantity",
        item_class="peripheral",
        manufacturer="ExampleCo",
        model="M100",
        default_reorder_point=5,
        default_reorder_quantity=20,
        actor="admin",
    )

    assert item.sku == "MOUSE-01"
    assert item.tracking_mode == TrackingMode.QUANTITY
    assert items.list()[0].sku == "MOUSE-01"
    assert items.apply_default_reorder_rule("MOUSE-01", location_id="STORE-A") is True

    with repo._connect() as conn:
        rule = conn.execute(
            "SELECT * FROM inventory_reorder_rules WHERE sku='MOUSE-01' AND location_id='STORE-A'"
        ).fetchone()
    assert rule["reorder_point"] == 5
    assert rule["reorder_quantity"] == 20


def test_serialized_item_rejects_quantity_reorder_defaults(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    items = ItemCatalogService(repo)

    with pytest.raises(InventoryDomainError, match="Serialized items"):
        items.create(
            sku="LAPTOP-01",
            name="Laptop",
            tracking_mode="serialized",
            item_class="laptop",
            default_reorder_point=1,
            default_reorder_quantity=2,
            actor="admin",
        )


def test_catalog_validates_tracking_mode_and_model(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    items = ItemCatalogService(repo)
    items.create(
        sku="LAPTOP-01",
        name="Laptop",
        tracking_mode="serialized",
        item_class="laptop",
        model="Model-X",
        actor="admin",
    )

    items.validate_po_item(
        sku="LAPTOP-01",
        tracking_mode=TrackingMode.SERIALIZED,
        model="Model-X",
    )
    with pytest.raises(InventoryDomainError, match="catalog requires serialized"):
        items.validate_po_item(
            sku="LAPTOP-01",
            tracking_mode=TrackingMode.QUANTITY,
            model="Model-X",
        )
    with pytest.raises(InventoryDomainError, match="catalog model"):
        items.validate_po_item(
            sku="LAPTOP-01",
            tracking_mode=TrackingMode.SERIALIZED,
            model="Wrong-Model",
        )


def test_item_deactivation_is_blocked_while_stock_exists(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    items = ItemCatalogService(repo)
    items.create(
        sku="MOUSE-01",
        name="Mouse",
        tracking_mode="quantity",
        item_class="peripheral",
        actor="admin",
    )
    with repo.transaction() as conn:
        repo.post_stock_transaction(
            StockTransaction(
                transaction_id="T-1",
                sku="MOUSE-01",
                quantity=5,
                transaction_type="receive",
                actor="seed",
                to_location="STORE-A",
            ),
            conn,
        )

    with pytest.raises(InventoryDomainError, match="stock on hand=5"):
        items.set_active("MOUSE-01", active=False)


def test_unused_item_can_be_deactivated_and_reactivated(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    items = ItemCatalogService(repo)
    items.create(
        sku="CABLE-01",
        name="USB Cable",
        tracking_mode="quantity",
        item_class="peripheral",
        actor="admin",
    )

    assert items.set_active("CABLE-01", active=False).active is False
    with pytest.raises(InventoryDomainError, match="inactive"):
        items.require_active("CABLE-01")
    assert items.set_active("CABLE-01", active=True).active is True

from pathlib import Path

import pytest

from src.inventory.domain import InventoryDomainError
from src.inventory.items import ItemCatalogService
from src.inventory.po_intake import PurchaseOrderIntakeWorkflow
from src.inventory.repository import InventoryRepository


def write_po(path: Path):
    path.write_text(
        "line_id,sku,description,quantity,tracking_mode,unit_price,model\n"
        "L1,LAPTOP-01,Laptop,2,serialized,1500,Model-X\n"
        "L2,MOUSE-01,Mouse,10,quantity,20,Mouse-X\n",
        encoding="utf-8",
    )


def seed_items(repo: InventoryRepository) -> ItemCatalogService:
    items = ItemCatalogService(repo)
    items.create(
        sku="LAPTOP-01",
        name="Laptop",
        tracking_mode="serialized",
        item_class="laptop",
        model="Model-X",
        actor="admin",
    )
    items.create(
        sku="MOUSE-01",
        name="Mouse",
        tracking_mode="quantity",
        item_class="peripheral",
        model="Mouse-X",
        actor="admin",
    )
    return items


def test_stage_does_not_create_po_until_confirmed(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_items(repo)
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)
    source = tmp_path / "po.csv"
    write_po(source)

    staged, preview = workflow.stage(
        purchase_order_id="PO-1",
        supplier="Supplier A",
        source_path=source,
        actor="U1",
    )

    with repo._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM inventory_purchase_orders WHERE purchase_order_id='PO-1'"
        ).fetchone()[0] == 0
    assert staged.staging_id in preview
    assert "Units: *12*" in preview

    result = workflow.confirm(staged.staging_id, actor="U1")
    assert "PO-1" in result
    with repo._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM inventory_po_lines WHERE purchase_order_id='PO-1'"
        ).fetchone()[0] == 2


def test_cancel_leaves_no_purchase_order(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_items(repo)
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)
    source = tmp_path / "po.csv"
    write_po(source)

    staged, _ = workflow.stage(
        purchase_order_id="PO-2",
        supplier="Supplier B",
        source_path=source,
        actor="U1",
    )
    workflow.cancel(staged.staging_id, actor="U1")

    with repo._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM inventory_purchase_orders WHERE purchase_order_id='PO-2'"
        ).fetchone()[0] == 0


def test_only_staging_user_can_confirm(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_items(repo)
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)
    source = tmp_path / "po.csv"
    write_po(source)

    staged, _ = workflow.stage(
        purchase_order_id="PO-3",
        supplier="Supplier C",
        source_path=source,
        actor="U1",
    )

    with pytest.raises(InventoryDomainError, match="Only the user"):
        workflow.confirm(staged.staging_id, actor="U2")


def test_duplicate_purchase_order_is_rejected(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_items(repo)
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)
    source = tmp_path / "po.csv"
    write_po(source)

    staged, _ = workflow.stage(
        purchase_order_id="PO-4",
        supplier="Supplier D",
        source_path=source,
        actor="U1",
    )
    workflow.confirm(staged.staging_id, actor="U1")

    with pytest.raises(InventoryDomainError, match="already exists"):
        workflow.stage(
            purchase_order_id="PO-4",
            supplier="Supplier D",
            source_path=source,
            actor="U1",
        )


def test_unknown_sku_and_tracking_mismatch_are_rejected(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    items = seed_items(repo)
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)

    unknown = tmp_path / "unknown.csv"
    unknown.write_text(
        "sku,quantity,tracking_mode\nNEW-SKU,1,quantity\n",
        encoding="utf-8",
    )
    with pytest.raises(InventoryDomainError, match="Unknown inventory SKU"):
        workflow.stage(
            purchase_order_id="PO-X",
            supplier="Supplier",
            source_path=unknown,
            actor="U1",
        )

    mismatch = tmp_path / "mismatch.csv"
    mismatch.write_text(
        "sku,quantity,tracking_mode,model\nLAPTOP-01,1,quantity,Model-X\n",
        encoding="utf-8",
    )
    with pytest.raises(InventoryDomainError, match="catalog requires serialized"):
        workflow.stage(
            purchase_order_id="PO-Y",
            supplier="Supplier",
            source_path=mismatch,
            actor="U1",
        )

    items.set_active("MOUSE-01", active=False)
    inactive = tmp_path / "inactive.csv"
    inactive.write_text(
        "sku,quantity,tracking_mode,model\nMOUSE-01,1,quantity,Mouse-X\n",
        encoding="utf-8",
    )
    with pytest.raises(InventoryDomainError, match="inactive"):
        workflow.stage(
            purchase_order_id="PO-Z",
            supplier="Supplier",
            source_path=inactive,
            actor="U1",
        )

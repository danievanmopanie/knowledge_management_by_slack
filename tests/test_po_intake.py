from pathlib import Path

import pytest

from src.inventory.domain import InventoryDomainError
from src.inventory.items import ItemCatalogService
from src.inventory.po_intake import PurchaseOrderIntakeWorkflow
from src.inventory.repository import InventoryRepository
from src.inventory.suppliers import SupplierService


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


def seed_supplier(repo: InventoryRepository, supplier_id: str = "SUP-A") -> SupplierService:
    suppliers = SupplierService(repo)
    suppliers.create(
        supplier_id=supplier_id,
        name=f"Supplier {supplier_id}",
        actor="admin",
    )
    return suppliers


def test_stage_does_not_create_po_until_confirmed(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_items(repo)
    seed_supplier(repo, "SUP-A")
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)
    source = tmp_path / "po.csv"
    write_po(source)

    staged, preview = workflow.stage(
        purchase_order_id="PO-1",
        supplier="SUP-A",
        source_path=source,
        actor="U1",
    )

    with repo._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM inventory_purchase_orders WHERE purchase_order_id='PO-1'"
        ).fetchone()[0] == 0
    assert staged.staging_id in preview
    assert "Units: *12*" in preview
    assert "SUP-A" in preview

    result = workflow.confirm(staged.staging_id, actor="U1")
    assert "PO-1" in result
    assert "SUP-A" in result
    with repo._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM inventory_po_lines WHERE purchase_order_id='PO-1'"
        ).fetchone()[0] == 2


def test_cancel_leaves_no_purchase_order(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_items(repo)
    seed_supplier(repo, "SUP-B")
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)
    source = tmp_path / "po.csv"
    write_po(source)

    staged, _ = workflow.stage(
        purchase_order_id="PO-2",
        supplier="SUP-B",
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
    seed_supplier(repo, "SUP-C")
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)
    source = tmp_path / "po.csv"
    write_po(source)

    staged, _ = workflow.stage(
        purchase_order_id="PO-3",
        supplier="SUP-C",
        source_path=source,
        actor="U1",
    )

    with pytest.raises(InventoryDomainError, match="Only the user"):
        workflow.confirm(staged.staging_id, actor="U2")


def test_duplicate_purchase_order_is_rejected(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_items(repo)
    seed_supplier(repo, "SUP-D")
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)
    source = tmp_path / "po.csv"
    write_po(source)

    staged, _ = workflow.stage(
        purchase_order_id="PO-4",
        supplier="SUP-D",
        source_path=source,
        actor="U1",
    )
    workflow.confirm(staged.staging_id, actor="U1")

    with pytest.raises(InventoryDomainError, match="already exists"):
        workflow.stage(
            purchase_order_id="PO-4",
            supplier="SUP-D",
            source_path=source,
            actor="U1",
        )


def test_unknown_sku_and_tracking_mismatch_are_rejected(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    items = seed_items(repo)
    seed_supplier(repo, "SUP-X")
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)

    unknown = tmp_path / "unknown.csv"
    unknown.write_text(
        "sku,quantity,tracking_mode\nNEW-SKU,1,quantity\n",
        encoding="utf-8",
    )
    with pytest.raises(InventoryDomainError, match="Unknown inventory SKU"):
        workflow.stage(
            purchase_order_id="PO-X",
            supplier="SUP-X",
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
            supplier="SUP-X",
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
            supplier="SUP-X",
            source_path=inactive,
            actor="U1",
        )


def test_unknown_and_inactive_supplier_are_rejected(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_items(repo)
    suppliers = seed_supplier(repo, "SUP-A")
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)
    source = tmp_path / "po.csv"
    write_po(source)

    with pytest.raises(InventoryDomainError, match="Unknown inventory supplier"):
        workflow.stage(
            purchase_order_id="PO-UNKNOWN",
            supplier="MADE-UP",
            source_path=source,
            actor="U1",
        )

    suppliers.set_active("SUP-A", active=False)
    with pytest.raises(InventoryDomainError, match="inactive"):
        workflow.stage(
            purchase_order_id="PO-INACTIVE",
            supplier="SUP-A",
            source_path=source,
            actor="U1",
        )


def test_supplier_is_revalidated_when_staged_po_is_confirmed(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    seed_items(repo)
    suppliers = seed_supplier(repo, "SUP-A")
    workflow = PurchaseOrderIntakeWorkflow(repository=repo)
    source = tmp_path / "po.csv"
    write_po(source)

    staged, _ = workflow.stage(
        purchase_order_id="PO-STAGED",
        supplier="SUP-A",
        source_path=source,
        actor="U1",
    )
    suppliers.set_active("SUP-A", active=False)

    with pytest.raises(InventoryDomainError, match="inactive"):
        workflow.confirm(staged.staging_id, actor="U1")

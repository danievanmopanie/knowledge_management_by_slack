import pytest

from src.inventory.domain import InventoryDomainError, TrackingMode
from src.inventory.procurement import PurchaseOrder, PurchaseOrderLine
from src.inventory.repository import InventoryRepository
from src.inventory.suppliers import SupplierService


def test_supplier_create_list_deactivate_and_reactivate(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    suppliers = SupplierService(repo)

    supplier = suppliers.create(
        supplier_id="dell-sa",
        name="Dell South Africa",
        contact_name="Jane.Smith",
        email="jane@example.com",
        phone="+27110000000",
        actor="admin",
    )
    assert supplier.supplier_id == "DELL-SA"
    assert supplier.active is True
    assert suppliers.list()[0].name == "Dell South Africa"

    assert suppliers.set_active("DELL-SA", active=False).active is False
    assert suppliers.list() == []
    assert suppliers.list(include_inactive=True)[0].supplier_id == "DELL-SA"
    assert suppliers.set_active("DELL-SA", active=True).active is True


def test_supplier_deactivation_is_blocked_while_open_po_exists(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    suppliers = SupplierService(repo)
    suppliers.create(
        supplier_id="DELL-SA",
        name="Dell South Africa",
        actor="admin",
    )
    repo.save_purchase_order(
        PurchaseOrder(
            purchase_order_id="PO-1",
            supplier="DELL-SA",
            created_by="U1",
            lines=[
                PurchaseOrderLine(
                    line_id="L1",
                    sku="LAPTOP-01",
                    description="Laptop",
                    quantity_ordered=1,
                    tracking_mode=TrackingMode.SERIALIZED,
                )
            ],
        )
    )

    with pytest.raises(InventoryDomainError, match="open/partially received PO"):
        suppliers.set_active("DELL-SA", active=False)


def test_unknown_and_inactive_supplier_validation(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    suppliers = SupplierService(repo)

    with pytest.raises(InventoryDomainError, match="Unknown inventory supplier"):
        suppliers.require_active("UNKNOWN")

    suppliers.create(
        supplier_id="DELL-SA",
        name="Dell South Africa",
        actor="admin",
    )
    suppliers.set_active("DELL-SA", active=False)
    with pytest.raises(InventoryDomainError, match="inactive"):
        suppliers.require_active("DELL-SA")

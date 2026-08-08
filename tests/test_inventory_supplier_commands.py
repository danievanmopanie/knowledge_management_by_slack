from src.inventory.commands import InventoryCommandService
from src.inventory.repository import InventoryRepository


def test_supplier_commands_manage_master_data(tmp_path):
    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)

    created = commands.execute(
        "create supplier DELL-SA name Dell South Africa contact Jane.Smith email jane@example.com phone +27110000000",
        actor="admin",
    )
    assert "DELL-SA" in created
    assert "Dell South Africa" in created

    detail = commands.execute("supplier DELL-SA", actor="U1")
    assert "Jane.Smith" in detail
    assert "jane@example.com" in detail
    assert "active" in detail

    listing = commands.execute("suppliers", actor="U1")
    assert "DELL-SA" in listing

    commands.execute("deactivate supplier DELL-SA", actor="admin")
    assert "inactive" in commands.execute("supplier DELL-SA", actor="U1")
    assert "DELL-SA" not in commands.execute("suppliers", actor="U1")
    assert "DELL-SA" in commands.execute("suppliers all", actor="U1")

    commands.execute("activate supplier DELL-SA", actor="admin")
    assert "active" in commands.execute("supplier DELL-SA", actor="U1")

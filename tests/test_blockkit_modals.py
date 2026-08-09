from src.agents.inventory.agent import CREATE_CUSTOMER_RE
from src.bot.blockkit import ids, modals
from src.inventory.commands import InventoryCommandService
from src.inventory.customers import CustomerCustodyService
from src.inventory.repository import InventoryRepository


def seed_locations(commands):
    commands.execute("create location SITE-A type site site SITE-A name Main Site", actor="admin")
    commands.execute(
        "create location STORE-A type storeroom site SITE-A name Main Store parent SITE-A",
        actor="admin",
    )


def test_issue_asset_modal_shapes_and_toggles_loan_date():
    view = modals.build_issue_asset_modal(channel_id="C1", thread_ts="123.45")
    assert view["callback_id"] == ids.MODAL_ISSUE_ASSET
    block_ids = {block["block_id"] for block in view["blocks"]}
    assert block_ids == {"asset", "recipient", "customer", "allocation"}

    with_loan = modals.build_issue_asset_modal(channel_id="C1", show_loan_date=True)
    assert "due" in {block["block_id"] for block in with_loan["blocks"]}


def test_issue_asset_command_from_state_dedicated():
    values = {
        "asset": {"value": {"value": "A-1042"}},
        "recipient": {"value": {"selected_user": "U123"}},
        "customer": {"value": {"value": "EMP-42"}},
        "allocation": {"value": {"selected_option": {"value": "dedicated"}}},
    }
    assert (
        modals.issue_asset_command_from_state(values)
        == "issue asset A-1042 to U123 customer EMP-42 dedicated"
    )


def test_issue_asset_command_from_state_loan():
    values = {
        "asset": {"value": {"value": "A-1042"}},
        "recipient": {"value": {"selected_user": "U123"}},
        "customer": {"value": {"value": "EMP-42"}},
        "allocation": {"value": {"selected_option": {"value": "loan"}}},
        "due": {"value": {"selected_date": "2026-08-20"}},
    }
    assert (
        modals.issue_asset_command_from_state(values)
        == "issue asset A-1042 to U123 customer EMP-42 loan until 2026-08-20"
    )


def test_create_customer_command_from_state_round_trips_into_real_parser(tmp_path):
    # Customer commands are matched by InventoryAgent's own CREATE_CUSTOMER_RE
    # (src/agents/inventory/agent.py) rather than InventoryCommandService, so the
    # round-trip guard here matches against that regex directly, then executes
    # via the same CustomerCustodyService the agent calls on a match.
    values = {
        "customer": {"value": {"value": "EMP-42"}},
        "type": {"value": {"selected_option": {"value": "employee"}}},
        "name": {"value": {"value": "Jane Smith"}},
        "site": {"value": {"value": ""}},
        "email": {"value": {"value": ""}},
    }
    command = modals.create_customer_command_from_state(values)
    assert command == "create customer EMP-42 type employee name Jane Smith"

    match = CREATE_CUSTOMER_RE.match(command)
    assert match is not None

    repo = InventoryRepository(tmp_path / "inventory.db")
    customer = CustomerCustodyService(repo).create(
        customer_id=match.group("customer"),
        customer_type=match.group("type"),
        name=match.group("name"),
        site=match.group("site") or "",
        email=match.group("email") or "",
        actor="U123",
    )
    assert customer.customer_id == "EMP-42" and customer.customer_type == "employee"


def test_create_location_command_from_state_round_trips_into_real_parser(tmp_path):
    values = {
        "location": {"value": {"value": "SITE-A"}},
        "type": {"value": {"selected_option": {"value": "site"}}},
        "site": {"value": {"value": "SITE-A"}},
        "name": {"value": {"value": "Main Site"}},
        "parent": {"value": {"value": ""}},
    }
    command = modals.create_location_command_from_state(values)
    assert command == "create location SITE-A type site site SITE-A name Main Site"

    repo = InventoryRepository(tmp_path / "inventory.db")
    commands = InventoryCommandService(repo)
    result = commands.execute(command, actor="admin")
    assert "SITE-A" in result


def test_return_asset_command_from_state():
    values = {"asset": {"value": {"value": "A-1042"}}}
    assert modals.return_asset_command_from_state(values) == "return asset A-1042"

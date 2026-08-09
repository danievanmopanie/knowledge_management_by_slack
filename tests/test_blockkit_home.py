from src.bot.blockkit import ids
from src.bot.blockkit.home import build_home_view


def test_home_view_has_expected_buttons():
    view = build_home_view()
    assert view["type"] == "home"
    action_ids = {
        element["action_id"]
        for block in view["blocks"]
        if block.get("type") == "actions"
        for element in block["elements"]
    }
    assert action_ids == {
        ids.HOME_OPEN_ISSUE_ASSET,
        ids.HOME_OPEN_RETURN_ASSET,
        ids.HOME_OPEN_CREATE_CUSTOMER,
        ids.HOME_OPEN_CREATE_LOCATION,
        ids.HOME_OPEN_INVENTORY_SUMMARY,
    }

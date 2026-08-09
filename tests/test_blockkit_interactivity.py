import asyncio
from unittest.mock import AsyncMock

from src.bot import interactivity
from src.bot.blockkit import actions, ids


class FakeApp:
    def __init__(self):
        self.events = {}
        self.actions = {}
        self.views = {}

    @staticmethod
    def _decorator(store, key):
        def register(handler):
            store[key] = handler
            return handler

        return register

    def event(self, name):
        return self._decorator(self.events, name)

    def action(self, action_id):
        return self._decorator(self.actions, action_id)

    def view(self, callback_id):
        return self._decorator(self.views, callback_id)


def registered_app():
    app = FakeApp()
    interactivity.register(app)
    return app


def fake_client():
    client = AsyncMock()
    client.views_open.return_value = {"view": {"id": "V123"}}
    client.views_push.return_value = {"view": {"id": "V124"}}
    return client


def confirmation_body(blocks):
    return {
        "actions": [{"value": "PO-STAGE-123"}],
        "channel": {"id": "CINV"},
        "message": {"ts": "123.45", "text": "PO preview", "blocks": blocks},
        "user": {"id": "U1"},
    }


def test_app_home_hides_actions_for_user_outside_allowlist(monkeypatch):
    monkeypatch.setattr(interactivity.settings, "inventory_interactive_allowed_user_ids", "UADMIN")
    app = registered_app()
    client = fake_client()

    asyncio.run(app.events["app_home_opened"]({"user": "UOTHER"}, client))

    view = client.views_publish.await_args.kwargs["view"]
    assert not any(block.get("type") == "actions" for block in view["blocks"])


def test_app_home_action_opens_access_denied_modal_for_user_outside_allowlist(monkeypatch):
    monkeypatch.setattr(interactivity.settings, "inventory_interactive_allowed_user_ids", "UADMIN")
    app = registered_app()
    client = fake_client()
    ack = AsyncMock()

    asyncio.run(
        app.actions[ids.HOME_OPEN_ISSUE_ASSET](
            ack,
            {"trigger_id": "T1", "user": {"id": "UOTHER"}},
            client,
        )
    )

    ack.assert_awaited_once()
    view = client.views_open.await_args.kwargs["view"]
    assert "not authorized" in view["blocks"][0]["text"]["text"]


def test_issue_asset_new_customer_pushes_nested_modal_and_prefills_id(monkeypatch):
    monkeypatch.setattr(interactivity.settings, "inventory_interactive_allowed_user_ids", "U1")
    app = registered_app()
    client = fake_client()
    ack = AsyncMock()
    body = {
        "trigger_id": "T1",
        "user": {"id": "U1"},
        "view": {
            "id": "VISSUE",
            "private_metadata": '{"channel_id": "CINV", "thread_ts": null}',
            "state": {
                "values": {
                    "customer": {"value": {"value": "EMP-42"}},
                }
            },
        },
    }

    asyncio.run(app.actions[ids.HOME_OPEN_CREATE_CUSTOMER](ack, body, client))

    ack.assert_awaited_once()
    client.views_open.assert_not_awaited()
    client.views_push.assert_awaited_once()
    pushed = client.views_push.await_args.kwargs["view"]
    customer_block = next(block for block in pushed["blocks"] if block["block_id"] == "customer")
    assert customer_block["element"]["initial_value"] == "EMP-42"


def test_inventory_summary_opens_loading_modal_before_running_query(monkeypatch):
    monkeypatch.setattr(interactivity.settings, "inventory_interactive_allowed_user_ids", "U1")
    app = registered_app()
    order = []

    async def ack():
        order.append("ack")

    async def run(*args, **kwargs):
        order.append("run")
        return "Inventory summary"

    client = fake_client()

    async def views_open(**kwargs):
        order.append("open")
        return {"view": {"id": "V123"}}

    async def views_update(**kwargs):
        order.append("update")

    client.views_open = views_open
    client.views_update = views_update
    monkeypatch.setattr(interactivity, "_run", run)

    asyncio.run(
        app.actions[ids.HOME_OPEN_INVENTORY_SUMMARY](
            ack,
            {"trigger_id": "T1", "user": {"id": "U1"}},
            client,
        )
    )

    assert order == ["ack", "open", "run", "update"]


def test_failed_confirmation_keeps_buttons_available(monkeypatch):
    app = registered_app()
    client = fake_client()
    ack = AsyncMock()
    blocks = actions.attach_confirm_cancel(
        "Confirm with `confirm po PO-STAGE-123` or cancel with `cancel po PO-STAGE-123`."
    )
    assert blocks is not None

    async def run(*args, **kwargs):
        return "Inventory rule prevented that operation: Only the staging user may confirm it."

    monkeypatch.setattr(interactivity, "_run", run)
    asyncio.run(app.actions[ids.CONFIRM_PO](ack, confirmation_body(blocks), client))

    client.chat_update.assert_not_awaited()
    client.chat_postMessage.assert_awaited_once()


def test_successful_confirmation_removes_only_its_buttons(monkeypatch):
    app = registered_app()
    client = fake_client()
    ack = AsyncMock()
    blocks = actions.attach_confirm_cancel(
        "Confirm with `confirm po PO-STAGE-123` or cancel with `cancel po PO-STAGE-123`."
    )
    assert blocks is not None

    async def run(*args, **kwargs):
        return "Purchase order `PO-1` confirmed with 1 line(s) for supplier `SUP-1`."

    monkeypatch.setattr(interactivity, "_run", run)
    asyncio.run(app.actions[ids.CONFIRM_PO](ack, confirmation_body(blocks), client))

    client.chat_update.assert_awaited_once()
    assert client.chat_update.await_args.kwargs["blocks"] == [blocks[0]]
    client.chat_postMessage.assert_awaited_once()

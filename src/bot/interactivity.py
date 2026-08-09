"""Slack Block Kit interactivity: App Home, modals, and Confirm/Cancel buttons.

Every handler here ends up calling the same `InventoryAgent.handle(message, context)`
entry point that the existing `@app.event("app_mention")` text flow uses in
`src/bot/app.py`, so domain error handling (`InventoryDomainError` ->
friendly text) and the `actor` audit trail stay identical between typed
commands and button/modal-driven ones. No command parsing or domain logic
lives in this module.
"""

from __future__ import annotations

import logging

from slack_bolt.async_app import AsyncApp

from src.bot.blockkit import actions, home, ids, modals
from src.bot.router import get_inventory_agent
from src.core.config import settings
from src.core.context import RequestContext
from src.core.errors import safe_error_message

logger = logging.getLogger(__name__)


def _home_channel_id() -> str:
    """Channel to post results into for actions opened from App Home (no channel context there)."""
    return settings.channel_inventory or ""


async def _run(command: str, *, user_id: str, channel_id: str, thread_ts: str | None) -> str:
    agent = get_inventory_agent()
    context = RequestContext.from_slack(channel_id=channel_id or "", user_id=user_id, thread_ts=thread_ts)
    try:
        return await agent.handle(command, context)
    except Exception:
        logger.exception("Inventory interactivity command failed request_id=%s", context.request_id)
        return safe_error_message(context.request_id)


async def _post_result(client, *, channel_id: str | None, thread_ts: str | None, text: str) -> None:
    if not channel_id:
        logger.warning("No channel available to post inventory interactivity result")
        return
    blocks = actions.attach_confirm_cancel(text)
    await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text, blocks=blocks)


def register(app: AsyncApp) -> None:
    """Register all Block Kit interactivity handlers on the given Bolt app."""

    @app.event("app_home_opened")
    async def on_home_opened(event, client):
        await client.views_publish(user_id=event["user"], view=home.build_home_view())

    @app.action(ids.HOME_OPEN_ISSUE_ASSET)
    async def open_issue_asset(ack, body, client):
        await ack()
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=modals.build_issue_asset_modal(channel_id=_home_channel_id(), thread_ts=None),
        )

    @app.action(ids.HOME_OPEN_RETURN_ASSET)
    async def open_return_asset(ack, body, client):
        await ack()
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=modals.build_return_asset_modal(channel_id=_home_channel_id(), thread_ts=None),
        )

    @app.action(ids.HOME_OPEN_CREATE_CUSTOMER)
    async def open_create_customer(ack, body, client):
        await ack()
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=modals.build_create_customer_modal(channel_id=_home_channel_id(), thread_ts=None),
        )

    @app.action(ids.HOME_OPEN_CREATE_LOCATION)
    async def open_create_location(ack, body, client):
        await ack()
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=modals.build_create_location_modal(channel_id=_home_channel_id(), thread_ts=None),
        )

    @app.action(ids.HOME_OPEN_INVENTORY_SUMMARY)
    async def open_inventory_summary(ack, body, client):
        await ack()
        summary = await _run(
            "inventory summary", user_id=body["user"]["id"], channel_id=_home_channel_id(), thread_ts=None
        )
        await client.views_open(
            trigger_id=body["trigger_id"], view=modals.build_inventory_summary_modal(summary)
        )

    @app.action(ids.ISSUE_ASSET_ALLOCATION_SELECT)
    async def toggle_loan_date(ack, body, client):
        await ack()
        values = body["view"]["state"]["values"]
        allocation = values["allocation"]["value"]["selected_option"]["value"]
        metadata = modals.load_private_metadata(body["view"].get("private_metadata"))
        await client.views_update(
            view_id=body["view"]["id"],
            hash=body["view"]["hash"],
            view=modals.build_issue_asset_modal(
                channel_id=metadata.get("channel_id") or "",
                thread_ts=metadata.get("thread_ts"),
                show_loan_date=(allocation == "loan"),
                initial_asset=(values.get("asset", {}).get("value", {}) or {}).get("value") or "",
                initial_recipient=(values.get("recipient", {}).get("value", {}) or {}).get("selected_user")
                or "",
                initial_customer=(values.get("customer", {}).get("value", {}) or {}).get("value") or "",
            ),
        )

    @app.view(ids.MODAL_ISSUE_ASSET)
    async def submit_issue_asset(ack, body, client, view):
        await ack()
        metadata = modals.load_private_metadata(view.get("private_metadata"))
        command = modals.issue_asset_command_from_state(view["state"]["values"])
        result = await _run(
            command,
            user_id=body["user"]["id"],
            channel_id=metadata.get("channel_id"),
            thread_ts=metadata.get("thread_ts"),
        )
        await _post_result(
            client, channel_id=metadata.get("channel_id"), thread_ts=metadata.get("thread_ts"), text=result
        )

    @app.view(ids.MODAL_RETURN_ASSET)
    async def submit_return_asset(ack, body, client, view):
        await ack()
        metadata = modals.load_private_metadata(view.get("private_metadata"))
        command = modals.return_asset_command_from_state(view["state"]["values"])
        result = await _run(
            command,
            user_id=body["user"]["id"],
            channel_id=metadata.get("channel_id"),
            thread_ts=metadata.get("thread_ts"),
        )
        await _post_result(
            client, channel_id=metadata.get("channel_id"), thread_ts=metadata.get("thread_ts"), text=result
        )

    @app.view(ids.MODAL_CREATE_CUSTOMER)
    async def submit_create_customer(ack, body, client, view):
        await ack()
        metadata = modals.load_private_metadata(view.get("private_metadata"))
        command = modals.create_customer_command_from_state(view["state"]["values"])
        result = await _run(
            command,
            user_id=body["user"]["id"],
            channel_id=metadata.get("channel_id"),
            thread_ts=metadata.get("thread_ts"),
        )
        await _post_result(
            client, channel_id=metadata.get("channel_id"), thread_ts=metadata.get("thread_ts"), text=result
        )

    @app.view(ids.MODAL_CREATE_LOCATION)
    async def submit_create_location(ack, body, client, view):
        await ack()
        metadata = modals.load_private_metadata(view.get("private_metadata"))
        command = modals.create_location_command_from_state(view["state"]["values"])
        result = await _run(
            command,
            user_id=body["user"]["id"],
            channel_id=metadata.get("channel_id"),
            thread_ts=metadata.get("thread_ts"),
        )
        await _post_result(
            client, channel_id=metadata.get("channel_id"), thread_ts=metadata.get("thread_ts"), text=result
        )

    async def _confirm_cancel(ack, body, client, *, command_prefix: str) -> None:
        await ack()
        action = body["actions"][0]
        staged_id = action["value"]
        channel_id = body["channel"]["id"]
        message_ts = body["message"]["ts"]
        result = await _run(
            f"{command_prefix} {staged_id}",
            user_id=body["user"]["id"],
            channel_id=channel_id,
            thread_ts=message_ts,
        )
        await client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=body["message"].get("text", ""),
            blocks=[
                block
                for block in body["message"].get("blocks", [])
                if block.get("type") != "actions"
            ],
        )
        await client.chat_postMessage(channel=channel_id, thread_ts=message_ts, text=result)

    @app.action(ids.CONFIRM_PO)
    async def confirm_po(ack, body, client):
        await _confirm_cancel(ack, body, client, command_prefix="confirm po")

    @app.action(ids.CANCEL_PO)
    async def cancel_po(ack, body, client):
        await _confirm_cancel(ack, body, client, command_prefix="cancel po")

    @app.action(ids.CONFIRM_RECEIPT)
    async def confirm_receipt(ack, body, client):
        await _confirm_cancel(ack, body, client, command_prefix="confirm receipt")

    @app.action(ids.CANCEL_RECEIPT)
    async def cancel_receipt(ack, body, client):
        await _confirm_cancel(ack, body, client, command_prefix="cancel receipt")

    @app.action(ids.CONFIRM_COUNT)
    async def confirm_count(ack, body, client):
        await _confirm_cancel(ack, body, client, command_prefix="confirm count")

    @app.action(ids.CANCEL_COUNT)
    async def cancel_count(ack, body, client):
        await _confirm_cancel(ack, body, client, command_prefix="cancel count")

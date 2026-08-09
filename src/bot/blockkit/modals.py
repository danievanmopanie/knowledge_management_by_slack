"""Modals for the highest-friction Inventory Agent commands.

Each `build_*_modal()` function returns a pure Block Kit view dict. Each
`*_command_from_state()` function is a pure translator from the
`view["state"]["values"]` shape Slack sends on `view_submission` into the
exact command string the existing regex-based command grammar
(`src/agents/inventory/agent.py`, `src/inventory/base_commands.py`) already
accepts. No Slack SDK calls and no I/O happen in this module — that's what
makes it unit-testable without a live Slack connection.
"""

from __future__ import annotations

import json

from src.bot.blockkit import ids

CUSTOMER_TYPES = ("employee", "contractor", "department", "external")
LOCATION_TYPES = ("site", "storeroom", "cage", "shelf", "bin", "receiving", "repair", "quarantine")


def _options(values: tuple[str, ...]) -> list[dict]:
    return [{"text": {"type": "plain_text", "text": value}, "value": value} for value in values]


def _private_metadata(channel_id: str, thread_ts: str | None) -> str:
    return json.dumps({"channel_id": channel_id, "thread_ts": thread_ts})


def load_private_metadata(raw: str | None) -> dict:
    if not raw:
        return {"channel_id": None, "thread_ts": None}
    try:
        return json.loads(raw)
    except ValueError:
        return {"channel_id": None, "thread_ts": None}


def build_issue_asset_modal(
    *,
    channel_id: str,
    thread_ts: str | None = None,
    show_loan_date: bool = False,
    initial_asset: str = "",
    initial_recipient: str = "",
    initial_customer: str = "",
) -> dict:
    blocks: list[dict] = [
        {
            "type": "input",
            "block_id": "asset",
            "label": {"type": "plain_text", "text": "Asset ID"},
            "element": {
                "type": "plain_text_input",
                "action_id": "value",
                "placeholder": {"type": "plain_text", "text": "A-1042"},
                **({"initial_value": initial_asset} if initial_asset else {}),
            },
        },
        {
            "type": "input",
            "block_id": "recipient",
            "label": {"type": "plain_text", "text": "Assign to"},
            "element": {
                "type": "users_select",
                "action_id": "value",
                **({"initial_user": initial_recipient} if initial_recipient else {}),
            },
        },
        {
            "type": "input",
            "block_id": "customer",
            "label": {"type": "plain_text", "text": "Customer ID"},
            "element": {
                "type": "plain_text_input",
                "action_id": "value",
                "placeholder": {"type": "plain_text", "text": "EMP-42"},
                **({"initial_value": initial_customer} if initial_customer else {}),
            },
        },
        {
            "type": "input",
            "block_id": "allocation",
            "dispatch_action": True,
            "label": {"type": "plain_text", "text": "Allocation"},
            "element": {
                "type": "static_select",
                "action_id": ids.ISSUE_ASSET_ALLOCATION_SELECT,
                "options": [
                    {"text": {"type": "plain_text", "text": "Dedicated"}, "value": "dedicated"},
                    {"text": {"type": "plain_text", "text": "Loan"}, "value": "loan"},
                ],
                **(
                    {
                        "initial_option": {
                            "text": {"type": "plain_text", "text": "Loan"},
                            "value": "loan",
                        }
                    }
                    if show_loan_date
                    else {}
                ),
            },
        },
    ]
    if show_loan_date:
        blocks.append(
            {
                "type": "input",
                "block_id": "due",
                "label": {"type": "plain_text", "text": "Loan due date"},
                "element": {"type": "datepicker", "action_id": "value"},
            }
        )
    return {
        "type": "modal",
        "callback_id": ids.MODAL_ISSUE_ASSET,
        "private_metadata": _private_metadata(channel_id, thread_ts),
        "title": {"type": "plain_text", "text": "Issue Asset"},
        "submit": {"type": "plain_text", "text": "Issue"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def issue_asset_command_from_state(values: dict) -> str:
    asset_id = values["asset"]["value"]["value"].strip()
    slack_user = values["recipient"]["value"]["selected_user"]
    customer_id = values["customer"]["value"]["value"].strip()
    allocation = values["allocation"]["value"]["selected_option"]["value"]
    if allocation == "loan":
        due = values["due"]["value"]["selected_date"]
        return f"issue asset {asset_id} to {slack_user} customer {customer_id} loan until {due}"
    return f"issue asset {asset_id} to {slack_user} customer {customer_id} dedicated"


def build_return_asset_modal(*, channel_id: str, thread_ts: str | None = None) -> dict:
    return {
        "type": "modal",
        "callback_id": ids.MODAL_RETURN_ASSET,
        "private_metadata": _private_metadata(channel_id, thread_ts),
        "title": {"type": "plain_text", "text": "Return Asset"},
        "submit": {"type": "plain_text", "text": "Return"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "asset",
                "label": {"type": "plain_text", "text": "Asset ID"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "placeholder": {"type": "plain_text", "text": "A-1042"},
                },
            },
        ],
    }


def return_asset_command_from_state(values: dict) -> str:
    asset_id = values["asset"]["value"]["value"].strip()
    return f"return asset {asset_id}"


def build_create_customer_modal(*, channel_id: str, thread_ts: str | None = None) -> dict:
    return {
        "type": "modal",
        "callback_id": ids.MODAL_CREATE_CUSTOMER,
        "private_metadata": _private_metadata(channel_id, thread_ts),
        "title": {"type": "plain_text", "text": "New Customer"},
        "submit": {"type": "plain_text", "text": "Create"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "customer",
                "label": {"type": "plain_text", "text": "Customer ID"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "placeholder": {"type": "plain_text", "text": "EMP-42"},
                },
            },
            {
                "type": "input",
                "block_id": "type",
                "label": {"type": "plain_text", "text": "Type"},
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "options": _options(CUSTOMER_TYPES),
                },
            },
            {
                "type": "input",
                "block_id": "name",
                "label": {"type": "plain_text", "text": "Name"},
                "element": {"type": "plain_text_input", "action_id": "value"},
            },
            {
                "type": "input",
                "block_id": "site",
                "optional": True,
                "label": {"type": "plain_text", "text": "Site"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "placeholder": {"type": "plain_text", "text": "SITE-A"},
                },
            },
            {
                "type": "input",
                "block_id": "email",
                "optional": True,
                "label": {"type": "plain_text", "text": "Email"},
                "element": {"type": "plain_text_input", "action_id": "value"},
            },
        ],
    }


def create_customer_command_from_state(values: dict) -> str:
    customer_id = values["customer"]["value"]["value"].strip()
    customer_type = values["type"]["value"]["selected_option"]["value"]
    name = values["name"]["value"]["value"].strip()
    site = (values.get("site", {}).get("value", {}) or {}).get("value") or ""
    email = (values.get("email", {}).get("value", {}) or {}).get("value") or ""
    command = f"create customer {customer_id} type {customer_type} name {name}"
    if site.strip():
        command += f" site {site.strip()}"
    if email.strip():
        command += f" email {email.strip()}"
    return command


def build_create_location_modal(*, channel_id: str, thread_ts: str | None = None) -> dict:
    return {
        "type": "modal",
        "callback_id": ids.MODAL_CREATE_LOCATION,
        "private_metadata": _private_metadata(channel_id, thread_ts),
        "title": {"type": "plain_text", "text": "New Location"},
        "submit": {"type": "plain_text", "text": "Create"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "location",
                "label": {"type": "plain_text", "text": "Location ID"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "placeholder": {"type": "plain_text", "text": "SHELF-B3"},
                },
            },
            {
                "type": "input",
                "block_id": "type",
                "label": {"type": "plain_text", "text": "Type"},
                "element": {
                    "type": "static_select",
                    "action_id": "value",
                    "options": _options(LOCATION_TYPES),
                },
            },
            {
                "type": "input",
                "block_id": "site",
                "label": {"type": "plain_text", "text": "Site"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "placeholder": {"type": "plain_text", "text": "SITE-A"},
                },
            },
            {
                "type": "input",
                "block_id": "name",
                "label": {"type": "plain_text", "text": "Name"},
                "element": {"type": "plain_text_input", "action_id": "value"},
            },
            {
                "type": "input",
                "block_id": "parent",
                "optional": True,
                "label": {"type": "plain_text", "text": "Parent location"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "placeholder": {"type": "plain_text", "text": "STORE-A"},
                },
            },
        ],
    }


def create_location_command_from_state(values: dict) -> str:
    location_id = values["location"]["value"]["value"].strip()
    location_type = values["type"]["value"]["selected_option"]["value"]
    site = values["site"]["value"]["value"].strip()
    name = values["name"]["value"]["value"].strip()
    parent = (values.get("parent", {}).get("value", {}) or {}).get("value") or ""
    command = f"create location {location_id} type {location_type} site {site} name {name}"
    if parent.strip():
        command += f" parent {parent.strip()}"
    return command


def build_inventory_summary_modal(summary_text: str) -> dict:
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Inventory Summary"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": summary_text}},
        ],
    }


def build_access_denied_modal() -> dict:
    """Explain why an App Home action is unavailable without exposing inventory data."""
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Inventory Access"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "You are not authorized to run inventory actions from App Home. "
                        "Contact an inventory administrator if you need access."
                    ),
                },
            }
        ],
    }

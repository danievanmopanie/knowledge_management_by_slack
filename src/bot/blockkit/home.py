"""App Home dashboard for the Inventory Agent."""

from __future__ import annotations

from src.bot.blockkit import ids


def build_home_view() -> dict:
    """Return the Block Kit view published to a user's App Home tab."""
    return {
        "type": "home",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Inventory Agent"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "Pick an action below, or mention me in "
                        "*#inventory* with a typed command for anything not listed here."
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": "inventory_home_actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Issue Asset"},
                        "action_id": ids.HOME_OPEN_ISSUE_ASSET,
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Return Asset"},
                        "action_id": ids.HOME_OPEN_RETURN_ASSET,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "New Customer"},
                        "action_id": ids.HOME_OPEN_CREATE_CUSTOMER,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "New Location"},
                        "action_id": ids.HOME_OPEN_CREATE_LOCATION,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Inventory Summary"},
                        "action_id": ids.HOME_OPEN_INVENTORY_SUMMARY,
                    },
                ],
            },
        ],
    }

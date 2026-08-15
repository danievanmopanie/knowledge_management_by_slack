"""Small structured-input modals for Frontend Support human-in-the-loop steps."""

from __future__ import annotations

import json

MODAL_INCIDENT_NUMBER = "frontend_incident_number_submit"
MODAL_CLARIFICATION_OTHER = "frontend_clarification_other_submit"


def _metadata(**values: str | None) -> str:
    return json.dumps({key: value for key, value in values.items() if value is not None})


def load_metadata(value: str | None) -> dict:
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def incident_number_modal(*, channel_id: str, thread_ts: str) -> dict:
    return {
        "type": "modal",
        "callback_id": MODAL_INCIDENT_NUMBER,
        "private_metadata": _metadata(channel_id=channel_id, thread_ts=thread_ts),
        "title": {"type": "plain_text", "text": "Link incident"},
        "submit": {"type": "plain_text", "text": "Link incident"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "incident_number",
                "label": {"type": "plain_text", "text": "ServiceNow incident number"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "placeholder": {"type": "plain_text", "text": "INC0012345"},
                },
            }
        ],
    }


def clarification_other_modal(
    *,
    channel_id: str,
    thread_ts: str,
    key: str,
    source_message_ts: str | None,
) -> dict:
    label = key.replace("_", " ").strip().title() or "Clarification"
    return {
        "type": "modal",
        "callback_id": MODAL_CLARIFICATION_OTHER,
        "private_metadata": _metadata(
            channel_id=channel_id,
            thread_ts=thread_ts,
            key=key,
            source_message_ts=source_message_ts,
        ),
        "title": {"type": "plain_text", "text": "Add detail"},
        "submit": {"type": "plain_text", "text": "Continue"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "clarification_value",
                "label": {"type": "plain_text", "text": label[:75]},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "multiline": False,
                },
            }
        ],
    }

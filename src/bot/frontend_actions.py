"""Block Kit controls for collaborative frontend-support knowledge capture."""

from __future__ import annotations

import json

from src.bot.blockkit.decisions import decision_actions

CAPTURE_RESOLUTION = "frontend_capture_resolution"
DISMISS_RESOLUTION = "frontend_dismiss_resolution"
ADD_INCIDENT_NUMBER = "frontend_add_incident_number"


def build_resolution_capture_blocks(channel_id: str, thread_ts: str) -> list[dict]:
    value = json.dumps({"channel_id": channel_id, "thread_ts": thread_ts})
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "It looks like this issue is resolved. *Capture this as reusable knowledge?*",
            },
        },
        decision_actions(
            block_id="frontend_resolution_capture",
            value=value,
            primary_action_id=CAPTURE_RESOLUTION,
            primary_label="Capture knowledge",
            secondary_action_id=DISMISS_RESOLUTION,
            secondary_label="Not now",
        ),
    ]


def build_incident_number_blocks(channel_id: str, thread_ts: str, *, resolved: bool = False) -> list[dict]:
    value = json.dumps({"channel_id": channel_id, "thread_ts": thread_ts})
    prompt = (
        "This looks resolved. Link the ServiceNow incident so the fix can be captured as referenceable knowledge."
        if resolved
        else "Link the ServiceNow incident so this support thread stays referenceable."
    )
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": prompt},
        },
        decision_actions(
            block_id="frontend_incident_number",
            value=value,
            primary_action_id=ADD_INCIDENT_NUMBER,
            primary_label="Add incident number",
        ),
    ]

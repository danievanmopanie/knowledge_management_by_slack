"""Block Kit controls for collaborative frontend-support knowledge capture."""

from __future__ import annotations

import json

from src.bot.blockkit.decisions import decision_actions

CAPTURE_RESOLUTION = "frontend_capture_resolution"
DISMISS_RESOLUTION = "frontend_dismiss_resolution"


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

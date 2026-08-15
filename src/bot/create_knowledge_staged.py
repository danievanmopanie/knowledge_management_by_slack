"""Block Kit preview shown before a staged incident CSV is confirmed."""

from __future__ import annotations


def staged_incident_blocks(summary: str) -> list[dict]:
    summary = (summary or "").strip()
    if len(summary) > 2800:
        summary = summary[:2797] + "..."
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📄 Incident CSV ready to build", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*What happens after confirmation*\n"
                    "1. Compare the snapshot with existing incident knowledge\n"
                    "2. Write new versions, state/assignment transitions and dwell history\n"
                    "3. Build deterministic graph relationships\n"
                    "4. Embed only new or semantically changed support text\n"
                    "5. Validate the build and publish it for retrieval\n\n"
                    "The build card will update in place while these steps run."
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Nothing is searchable until you confirm the staged upload.",
                }
            ],
        },
    ]

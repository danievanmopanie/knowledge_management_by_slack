"""Block Kit views for the natural-language Builder coding harness."""

from __future__ import annotations

from typing import Any


_STATUS_COPY = {
    "running": ("🛠️", "Working on it"),
    "repairing": ("🧪", "Fixing validation failures"),
    "validated": ("✅", "Local validation passed"),
    "answered": ("💬", "Answered from the repository"),
    "completed": ("✅", "Ready for review"),
    "failed": ("❌", "Builder stopped"),
}


def builder_status_blocks(
    *,
    task_id: str,
    status: str,
    summary: str,
    branch_name: str | None = None,
    validation: str | None = None,
    repair_attempt: str | None = None,
    pr_url: str | None = None,
) -> list[dict[str, Any]]:
    """Return a compact persistent progress card for one Builder turn.

    UX rule: conversation stays conversational. Block Kit is reserved for
    durable state, progress, choices and outcomes. The worker updates one card
    in place instead of posting a stream of operational status messages.
    """
    icon, title = _STATUS_COPY.get(status, ("🤖", "Builder"))
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{icon} {title}", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary[:2900]},
        },
    ]

    fields: list[dict[str, str]] = [
        {"type": "mrkdwn", "text": f"*Turn*\n`{task_id}`"},
    ]
    if branch_name:
        fields.append({"type": "mrkdwn", "text": f"*Branch*\n`{branch_name}`"})
    if validation:
        fields.append({"type": "mrkdwn", "text": f"*Validation*\n{validation}"})
    if repair_attempt:
        fields.append({"type": "mrkdwn", "text": f"*Repair*\n{repair_attempt}"})
    blocks.append({"type": "section", "fields": fields[:10]})

    if pr_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open pull request", "emoji": True},
                        "url": pr_url,
                        "action_id": "builder_open_pr",
                        "style": "primary",
                    }
                ],
            }
        )

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "_Builder runs on-device. Code is only published after the configured local gates pass._",
                }
            ],
        }
    )
    return blocks

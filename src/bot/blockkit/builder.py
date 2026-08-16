"""Block Kit views for the natural-language Builder coding harness."""

from __future__ import annotations

from typing import Any

from src.bot.blockkit import ids

_STATUS_COPY = {
    "running": ("🛠️", "Working on it"),
    "repairing": ("🧪", "Fixing validation failures"),
    "validated": ("✅", "Local validation passed"),
    "answered": ("💬", "Answered from the repository"),
    "completed": ("✅", "Ready for review"),
    "failed": ("❌", "Builder stopped"),
    "cancelled": ("🛑", "Cancelled"),
    "timed_out": ("⏱️", "Stopped: turn deadline reached"),
    "deployed": ("🚀", "Merged & deployed"),
    "deploy_failed": ("❌", "Merge & Deploy failed"),
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
    current_step: str | None = None,
    show_cancel: bool = False,
    show_merge_deploy: bool = False,
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

    if current_step:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"⏱️ _{current_step}_"}],
            }
        )

    action_elements: list[dict[str, Any]] = []
    if pr_url:
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open pull request", "emoji": True},
                "url": pr_url,
                "action_id": "builder_open_pr",
                "style": "primary",
            }
        )
    if show_merge_deploy:
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Merge & Deploy", "emoji": True},
                "action_id": ids.BUILDER_MERGE_DEPLOY,
                "value": task_id,
                "style": "primary",
                "confirm": {
                    "title": {"type": "plain_text", "text": "Merge and deploy?"},
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "This merges the pull request and restarts the configured GX10 "
                            "service(s). This is not easily reversible."
                        ),
                    },
                    "confirm": {"type": "plain_text", "text": "Merge & Deploy"},
                    "deny": {"type": "plain_text", "text": "Cancel"},
                },
            }
        )
    if show_cancel:
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Cancel", "emoji": True},
                "action_id": ids.BUILDER_CANCEL_TURN,
                "value": task_id,
                "style": "danger",
            }
        )
    if action_elements:
        blocks.append({"type": "actions", "elements": action_elements})

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

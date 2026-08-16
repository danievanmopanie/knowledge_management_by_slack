"""Block Kit views for the natural-language Builder coding harness."""

from __future__ import annotations

from typing import Any


_STATUS_COPY = {
    "queued": ("⏳", "Queued"),
    "running": ("🛠️", "Working on it"),
    "repairing": ("🧪", "Fixing validation failures"),
    "validated": ("✅", "Local validation passed"),
    "answered": ("💬", "Answered from the repository"),
    "completed": ("✅", "Ready for review"),
    "failed": ("❌", "Builder stopped"),
    "cancelled": ("⏹️", "Builder cancelled"),
}


def _duration(seconds: int | None) -> str:
    value = max(0, int(seconds or 0))
    minutes, secs = divmod(value, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def builder_status_blocks(
    *,
    task_id: str,
    status: str,
    summary: str,
    branch_name: str | None = None,
    validation: str | None = None,
    repair_attempt: str | None = None,
    pr_url: str | None = None,
    elapsed_seconds: int | None = None,
    idle_seconds: int | None = None,
    heartbeat: bool = False,
) -> list[dict[str, Any]]:
    """Return one persistent progress card for a Builder turn.

    Project UX rule: background work must never look frozen. One Block Kit card
    is updated in-place for phase changes and liveness heartbeats instead of
    posting a stream of operational messages.
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
    if elapsed_seconds is not None:
        fields.append({"type": "mrkdwn", "text": f"*Elapsed*\n{_duration(elapsed_seconds)}"})
    if validation:
        fields.append({"type": "mrkdwn", "text": f"*Validation*\n{validation}"})
    if repair_attempt:
        fields.append({"type": "mrkdwn", "text": f"*Repair*\n{repair_attempt}"})
    blocks.append({"type": "section", "fields": fields[:10]})

    if heartbeat:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"🟢 _Still working — last phase activity {_duration(idle_seconds)} ago._"
                        ),
                    }
                ],
            }
        )

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
                    "text": "_Builder runs on-device. This card stays live while work is active; code is only published after the configured local gates pass._",
                }
            ],
        }
    )
    return blocks

"""Block Kit previews shown before staged knowledge is confirmed."""

from __future__ import annotations

import re

from src.bot.blockkit import ids
from src.bot.blockkit.decisions import decision_actions

_STAGE_ID_RE = re.compile(r"\b(stg_[A-Za-z0-9]+)\b")


def _stage_ids(summary: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for match in _STAGE_ID_RE.finditer(summary or ""):
        stage_id = match.group(1)
        if stage_id not in seen:
            seen.add(stage_id)
            result.append(stage_id)
    return result


def _decision_blocks(summary: str) -> list[dict]:
    stage_ids = _stage_ids(summary)
    blocks: list[dict] = []
    multiple = len(stage_ids) > 1
    for stage_id in stage_ids:
        blocks.append(
            decision_actions(
                block_id=f"create_knowledge_stage_{stage_id}",
                value=stage_id,
                primary_action_id=ids.CREATE_KNOWLEDGE_CONFIRM_STAGE,
                primary_label=(f"Build {stage_id}" if multiple else "Build knowledge"),
                secondary_action_id=ids.CREATE_KNOWLEDGE_CANCEL_STAGE,
                secondary_label="Cancel",
                secondary_style="danger",
            )
        )
    return blocks


def staged_knowledge_blocks(summary: str, *, incident: bool) -> list[dict]:
    """Render a staged-upload card with button-first human approval."""
    summary = (summary or "").strip()
    if len(summary) > 2800:
        summary = summary[:2797] + "..."
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📄 Incident CSV ready to build" if incident else "📄 Knowledge upload ready",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary},
        },
    ]
    blocks.extend(_decision_blocks(summary))
    if incident:
        blocks.extend(
            [
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
            ]
        )
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "Nothing is searchable until you approve this upload. "
                        "Typed commands remain available as a fallback."
                    ),
                }
            ],
        }
    )
    return blocks


def staged_incident_blocks(summary: str) -> list[dict]:
    return staged_knowledge_blocks(summary, incident=True)


def staged_upload_blocks(summary: str) -> list[dict]:
    return staged_knowledge_blocks(summary, incident=False)

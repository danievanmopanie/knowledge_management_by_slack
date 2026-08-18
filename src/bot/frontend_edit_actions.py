"""Block Kit rendering for governed revisions of existing knowledge articles."""

from __future__ import annotations

import json

from src.bot.knowledge_governance_interactivity import build_governance_blocks

APPROVE_KNOWLEDGE_EDIT = "knowledge_edit_approve_publish"
DISMISS_KNOWLEDGE_EDIT = "knowledge_edit_dismiss"
_DECISION_BLOCK = "knowledge_edit:decision"
_MAX_NOTE_CHARS = 1200
_MAX_PREVIEW_CHARS = 1800


def _task_value(task: dict) -> str:
    return json.dumps({"request_id": int(task["id"])})


def build_knowledge_edit_card(
    task: dict,
    *,
    owner_user_id: str | None = None,
    pending_reviews: list[dict] | None = None,
) -> list[dict]:
    """Render one persistent review card for a staged article revision.

    The card appears while drafting is still in progress. Governance controls are
    available immediately and stay attached when the same message is refreshed
    with the completed, stale, dismissed or published state.
    """
    status = str(task.get("status") or "drafting")
    request_id = str(task.get("request_id") or f"KE-{int(task['id']):05d}")
    note = str(task.get("edit_note") or "").strip()[:_MAX_NOTE_CHARS]
    proposed = str(task.get("proposed_text") or "").strip()

    blocks: list[dict] = [
        {
            "type": "section",
            "block_id": "knowledge_edit:header",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{request_id} · Existing knowledge revision*\n"
                    f"*Article:* {task['document_title']}\n"
                    f"*Requested by:* <@{task['requested_by']}>"
                ),
            },
        },
        {
            "type": "section",
            "block_id": "knowledge_edit:note",
            "text": {
                "type": "mrkdwn",
                "text": f"*Field correction*\n{note or '_No correction note supplied._'}",
            },
        },
    ]

    if status == "drafting":
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:draft",
                "text": {
                    "type": "mrkdwn",
                    "text": "⏳ *Drafting focused revision…*\n_You can assign ownership or technical review while this runs._",
                },
            }
        )
    elif status == "draft_failed":
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:draft",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "⚠️ *Drafting failed — the article was not changed.*\n"
                        "_Ownership and review assignments are preserved._"
                    ),
                },
            }
        )
    elif status == "stale":
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:draft",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "⚠️ *Stale draft — not published.*\n"
                        "_The article changed after this draft was created. A fresh revision is required before publication._"
                    ),
                },
            }
        )
    elif status == "published":
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:draft",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"✅ *Published by <@{task.get('decided_by')}>.*\n"
                        f"New governed version: `{task.get('published_version_id') or 'unknown'}`"
                    ),
                },
            }
        )
    elif status == "dismissed":
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:draft",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🚫 *Dismissed by <@{task.get('decided_by')}> — no article change was published.*",
                },
            }
        )
    else:
        preview = proposed[:_MAX_PREVIEW_CHARS]
        if len(proposed) > _MAX_PREVIEW_CHARS:
            preview = preview.rstrip() + "\n…"
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:draft",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Proposed revision preview*\n{preview or '_Draft unavailable._'}",
                },
            }
        )
        if status == "review":
            value = _task_value(task)
            blocks.append(
                {
                    "type": "actions",
                    "block_id": _DECISION_BLOCK,
                    "elements": [
                        {
                            "type": "button",
                            "action_id": APPROVE_KNOWLEDGE_EDIT,
                            "text": {"type": "plain_text", "text": "Approve & publish"},
                            "style": "primary",
                            "value": value,
                        },
                        {
                            "type": "button",
                            "action_id": DISMISS_KNOWLEDGE_EDIT,
                            "text": {"type": "plain_text", "text": "Dismiss"},
                            "style": "danger",
                            "value": value,
                            "confirm": {
                                "title": {"type": "plain_text", "text": "Dismiss revision?"},
                                "text": {
                                    "type": "mrkdwn",
                                    "text": "This closes the proposed revision without changing the governed article.",
                                },
                                "confirm": {"type": "plain_text", "text": "Dismiss"},
                                "deny": {"type": "plain_text", "text": "Cancel"},
                            },
                        },
                    ],
                }
            )

    blocks.extend(
        build_governance_blocks(
            document_id=str(task["document_id"]),
            owner_user_id=owner_user_id,
            pending_reviews=pending_reviews or [],
        )
    )
    return blocks

"""Block Kit rendering for governed revisions of existing knowledge articles."""

from __future__ import annotations

import difflib
import json

from src.bot.knowledge_governance_interactivity import build_governance_blocks

APPROVE_KNOWLEDGE_EDIT = "knowledge_edit_approve_publish"
APPLY_REVIEW_FEEDBACK = "knowledge_edit_apply_review_feedback"
VIEW_FULL_KNOWLEDGE_DRAFT = "knowledge_edit_view_full_draft"
DISMISS_KNOWLEDGE_EDIT = "knowledge_edit_dismiss"
_DECISION_BLOCK = "knowledge_edit:decision"
_MAX_NOTE_CHARS = 1200
_MAX_DIFF_LINES = 12
_MAX_DIFF_CHARS = 1800


def _task_value(task: dict) -> str:
    return json.dumps({"request_id": int(task["id"])})


def compact_revision_diff(current_text: str, proposed_text: str) -> str:
    """Render only locally-computed changed lines for a fast Slack review preview."""
    current_lines = (current_text or "").splitlines()
    proposed_lines = (proposed_text or "").splitlines()
    changed = [
        line
        for line in difflib.ndiff(current_lines, proposed_lines)
        if line.startswith("- ") or line.startswith("+ ")
    ]
    if not changed:
        return "_No textual line changes detected._"
    visible = changed[:_MAX_DIFF_LINES]
    text = "\n".join(visible)
    if len(changed) > _MAX_DIFF_LINES:
        text += f"\n… {len(changed) - _MAX_DIFF_LINES} more changed lines"
    if len(text) > _MAX_DIFF_CHARS:
        text = text[:_MAX_DIFF_CHARS].rstrip() + "\n…"
    return f"```{text}```"


def build_knowledge_edit_card(
    task: dict,
    *,
    owner_user_id: str | None = None,
    pending_reviews: list[dict] | None = None,
    completed_reviews: list[dict] | None = None,
    current_text: str = "",
) -> list[dict]:
    """Render one persistent review card for a staged article revision."""
    status = str(task.get("status") or "drafting")
    request_id = str(task.get("request_id") or f"KE-{int(task['id']):05d}")
    note = str(task.get("edit_note") or "").strip()[:_MAX_NOTE_CHARS]
    proposed = str(task.get("proposed_text") or "").strip()
    completed = completed_reviews or []
    applied_ids = {int(item) for item in task.get("applied_review_ids") or []}
    unapplied_completed = [
        item for item in completed if int(item.get("id") or 0) not in applied_ids
    ]

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
    elif status == "feedback_drafting":
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:draft",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "⏳ *Applying completed technical review feedback…*\n"
                        "_The existing draft remains governed and nothing is published until the owner approves it._"
                    ),
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
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:draft",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*What changed*\n"
                        + compact_revision_diff(current_text, proposed)
                    ),
                },
            }
        )
        if status == "review":
            value = _task_value(task)
            elements: list[dict] = [
                {
                    "type": "button",
                    "action_id": VIEW_FULL_KNOWLEDGE_DRAFT,
                    "text": {"type": "plain_text", "text": "View full draft"},
                    "value": value,
                }
            ]
            if unapplied_completed:
                elements.append(
                    {
                        "type": "button",
                        "action_id": APPLY_REVIEW_FEEDBACK,
                        "text": {"type": "plain_text", "text": "Apply review feedback"},
                        "value": value,
                    }
                )
            elements.extend(
                [
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
                ]
            )
            blocks.append(
                {
                    "type": "actions",
                    "block_id": _DECISION_BLOCK,
                    "elements": elements,
                }
            )

    blocks.extend(
        build_governance_blocks(
            document_id=str(task["document_id"]),
            owner_user_id=owner_user_id,
            pending_reviews=pending_reviews or [],
            completed_reviews=completed,
        )
    )
    return blocks

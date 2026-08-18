"""Block Kit rendering for governed revisions of existing knowledge articles."""

from __future__ import annotations

import difflib
import json

from src.bot.knowledge_governance_interactivity import REQUEST_REVIEW, build_governance_blocks

APPROVE_KNOWLEDGE_EDIT = "knowledge_edit_approve_publish"
APPLY_REVIEW_FEEDBACK = "knowledge_edit_apply_review_feedback"
VIEW_FULL_KNOWLEDGE_DRAFT = "knowledge_edit_view_full_draft"
DISMISS_KNOWLEDGE_EDIT = "knowledge_edit_dismiss"
DRAFT_KNOWLEDGE_EDIT_WITH_AI = "knowledge_edit_draft_with_ai"
EDIT_KNOWLEDGE_EDIT_MANUALLY = "knowledge_edit_edit_manually"
_DECISION_BLOCK = "knowledge_edit:decision"
_WORKFLOW_BLOCK = "knowledge_edit:workflow"
_MAX_NOTE_CHARS = 1200
_MAX_DIFF_LINES = 12
_MAX_DIFF_CHARS = 1800


def _task_value(task: dict) -> str:
    return json.dumps({"request_id": int(task["id"])})


def _review_request_value(task: dict) -> str:
    return json.dumps(
        {
            "document_id": str(task["document_id"]),
            "shared_channel_id": str(task.get("shared_channel_id") or ""),
            "shared_message_ts": str(task.get("shared_message_ts") or ""),
        }
    )


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
    """Render one persistent, guided review card for a governed article revision."""
    status = str(task.get("status") or "awaiting_action")
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
                    f"*{request_id} · Knowledge revision*\n"
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
                "text": f"*Correction requested*\n{note or '_No correction note supplied._'}",
            },
        },
    ]

    value = _task_value(task)

    if status == "awaiting_action":
        blocks.extend(
            [
                {
                    "type": "section",
                    "block_id": "knowledge_edit:state",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "🟡 *Action needed — not published*\n"
                            "Choose how you want to create the proposed revision. "
                            "No AI work has started yet."
                        ),
                    },
                },
                {
                    "type": "actions",
                    "block_id": _WORKFLOW_BLOCK,
                    "elements": [
                        {
                            "type": "button",
                            "action_id": DRAFT_KNOWLEDGE_EDIT_WITH_AI,
                            "text": {"type": "plain_text", "text": "✨ Draft with AI"},
                            "style": "primary",
                            "value": value,
                        },
                        {
                            "type": "button",
                            "action_id": EDIT_KNOWLEDGE_EDIT_MANUALLY,
                            "text": {"type": "plain_text", "text": "✏️ Edit manually"},
                            "value": value,
                        },
                        {
                            "type": "button",
                            "action_id": DISMISS_KNOWLEDGE_EDIT,
                            "text": {"type": "plain_text", "text": "Dismiss"},
                            "value": value,
                        },
                    ],
                },
                {
                    "type": "context",
                    "block_id": "knowledge_edit:next",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "*Next:* create a draft → optionally request technical review → approve & publish.",
                        }
                    ],
                },
            ]
        )
    elif status == "drafting":
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:state",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "⏳ *AI drafting in progress — not published*\n"
                        "Atlas is preparing a focused revision from the approved article and the correction request."
                    ),
                },
            }
        )
    elif status == "feedback_drafting":
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:state",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "⏳ *Applying technical review feedback — not published*\n"
                        "The existing draft remains governed. Publication still requires explicit owner approval."
                    ),
                },
            }
        )
    elif status == "draft_failed":
        blocks.extend(
            [
                {
                    "type": "section",
                    "block_id": "knowledge_edit:state",
                    "text": {
                        "type": "mrkdwn",
                        "text": "⚠️ *Drafting failed — not published*\nChoose another drafting path or dismiss the revision.",
                    },
                },
                {
                    "type": "actions",
                    "block_id": _WORKFLOW_BLOCK,
                    "elements": [
                        {
                            "type": "button",
                            "action_id": DRAFT_KNOWLEDGE_EDIT_WITH_AI,
                            "text": {"type": "plain_text", "text": "Retry AI draft"},
                            "value": value,
                        },
                        {
                            "type": "button",
                            "action_id": EDIT_KNOWLEDGE_EDIT_MANUALLY,
                            "text": {"type": "plain_text", "text": "Edit manually"},
                            "style": "primary",
                            "value": value,
                        },
                        {
                            "type": "button",
                            "action_id": DISMISS_KNOWLEDGE_EDIT,
                            "text": {"type": "plain_text", "text": "Dismiss"},
                            "value": value,
                        },
                    ],
                },
            ]
        )
    elif status == "stale":
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:state",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "⚠️ *Stale revision — not published*\n"
                        "The governed article changed after this revision began. Start a fresh correction before publication."
                    ),
                },
            }
        )
    elif status == "published":
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:state",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"✅ *Published by <@{task.get('decided_by')}>*\n"
                        f"Governed version: `{task.get('published_version_id') or 'unknown'}`"
                    ),
                },
            }
        )
    elif status == "dismissed":
        blocks.append(
            {
                "type": "section",
                "block_id": "knowledge_edit:state",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🚫 *Dismissed by <@{task.get('decided_by')}> — no article change was published.*",
                },
            }
        )
    else:
        blocks.extend(
            [
                {
                    "type": "section",
                    "block_id": "knowledge_edit:state",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🟢 *Draft ready for review — not published*",
                    },
                },
                {
                    "type": "section",
                    "block_id": "knowledge_edit:draft",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*What changed*\n" + compact_revision_diff(current_text, proposed),
                    },
                },
            ]
        )
        if status == "review":
            elements: list[dict] = [
                {
                    "type": "button",
                    "action_id": VIEW_FULL_KNOWLEDGE_DRAFT,
                    "text": {"type": "plain_text", "text": "View full draft"},
                    "value": value,
                },
                {
                    "type": "button",
                    "action_id": EDIT_KNOWLEDGE_EDIT_MANUALLY,
                    "text": {"type": "plain_text", "text": "Edit manually"},
                    "value": value,
                },
            ]
            if str(task.get("shared_channel_id") or "") and str(task.get("shared_message_ts") or ""):
                elements.append(
                    {
                        "type": "button",
                        "action_id": REQUEST_REVIEW,
                        "text": {"type": "plain_text", "text": "Request technical review"},
                        "value": _review_request_value(task),
                    }
                )
            if unapplied_completed:
                elements.append(
                    {
                        "type": "button",
                        "action_id": APPLY_REVIEW_FEEDBACK,
                        "text": {"type": "plain_text", "text": "Apply review feedback"},
                        "value": value,
                    }
                )
            blocks.append(
                {
                    "type": "actions",
                    "block_id": "knowledge_edit:work",
                    "elements": elements,
                }
            )
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
                            "confirm": {
                                "title": {"type": "plain_text", "text": "Publish this revision?"},
                                "text": {
                                    "type": "mrkdwn",
                                    "text": "This creates a new governed knowledge version. The current published article will be superseded.",
                                },
                                "confirm": {"type": "plain_text", "text": "Publish revision"},
                                "deny": {"type": "plain_text", "text": "Keep reviewing"},
                            },
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
            blocks.append(
                {
                    "type": "context",
                    "block_id": "knowledge_edit:next",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "*Next:* edit or review as needed. Only *Approve & publish* creates a governed version.",
                        }
                    ],
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

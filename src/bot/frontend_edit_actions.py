"""Block Kit controls for flagging and reviewing an existing knowledge article triggered from #frontend-support."""

from __future__ import annotations

import json

PROPOSE_KNOWLEDGE_EDIT = "frontend_propose_knowledge_edit"
DISMISS_KNOWLEDGE_EDIT_PROMPT = "frontend_dismiss_knowledge_edit_prompt"

APPROVE_KNOWLEDGE_EDIT = "frontend_approve_knowledge_edit"
ASSIGN_KNOWLEDGE_EDIT = "frontend_assign_knowledge_edit"
DISMISS_KNOWLEDGE_EDIT = "frontend_dismiss_knowledge_edit"

KNOWLEDGE_EDIT_PROMPT_BLOCK_ID = "frontend_knowledge_edit_prompt"
KNOWLEDGE_EDIT_ACTIONS_BLOCK_PREFIX = "frontend_knowledge_edit_actions"
KNOWLEDGE_EDIT_ASSIGN_BLOCK_PREFIX = "frontend_knowledge_edit_assign"

_MAX_NOTE_CHARS = 1500
_MAX_PREVIEW_CHARS = 2500


def build_edit_prompt_blocks(*, channel_id: str, thread_ts: str, document_title: str, edit_note: str) -> list[dict]:
    """In-thread confirmation before anything is posted to #create-knowledge.

    The note travels in the button value (not re-read from Slack history later) so
    the drafted revision uses exactly what the technician said, even if the thread
    moves on before the button is clicked.
    """
    value = json.dumps(
        {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "edit_note": edit_note[:_MAX_NOTE_CHARS],
        }
    )
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":memo: Want me to flag *{document_title}* for review with this note?",
            },
        },
        {
            "type": "actions",
            "block_id": KNOWLEDGE_EDIT_PROMPT_BLOCK_ID,
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Flag for review"},
                    "style": "primary",
                    "action_id": PROPOSE_KNOWLEDGE_EDIT,
                    "value": value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Not now"},
                    "action_id": DISMISS_KNOWLEDGE_EDIT_PROMPT,
                    "value": value,
                },
            ],
        },
    ]


def _request_value(request_id: int) -> str:
    return json.dumps({"request_id": request_id})


def build_knowledge_edit_review_blocks(task: dict) -> list[dict]:
    """Render a flagged edit as a reviewable card: the note, the drafted revision preview, and controls."""
    preview = (task.get("proposed_text") or "").strip()
    if len(preview) > _MAX_PREVIEW_CHARS:
        preview = preview[:_MAX_PREVIEW_CHARS].rstrip() + "\n…"

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Knowledge edit {task['request_id']} — flagged from #frontend-support*\n"
                    f"*Article:* {task['document_title']}\n"
                    f"*Requested by:* <@{task['requested_by']}>"
                ),
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Technician's note:*\n{task['edit_note']}"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Drafted revision:*\n{preview or '_No revision could be drafted._'}",
            },
        },
        {
            "type": "actions",
            "block_id": f"{KNOWLEDGE_EDIT_ACTIONS_BLOCK_PREFIX}:{task['id']}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve revision"},
                    "style": "primary",
                    "action_id": APPROVE_KNOWLEDGE_EDIT,
                    "value": _request_value(task["id"]),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Dismiss"},
                    "style": "danger",
                    "action_id": DISMISS_KNOWLEDGE_EDIT,
                    "value": _request_value(task["id"]),
                },
            ],
        },
        {
            "type": "actions",
            "block_id": f"{KNOWLEDGE_EDIT_ASSIGN_BLOCK_PREFIX}:{task['id']}",
            "elements": [
                {
                    "type": "users_select",
                    "action_id": ASSIGN_KNOWLEDGE_EDIT,
                    "placeholder": {"type": "plain_text", "text": "Assign a teammate to review"},
                }
            ],
        },
    ]

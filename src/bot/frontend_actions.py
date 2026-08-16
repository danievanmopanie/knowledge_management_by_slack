"""Block Kit controls for collaborative frontend-support knowledge capture."""

from __future__ import annotations

import json

CAPTURE_RESOLUTION = "frontend_capture_resolution"
DISMISS_RESOLUTION = "frontend_dismiss_resolution"

PUBLISH_KNOWLEDGE = "frontend_publish_knowledge"
UPDATE_EXISTING_KNOWLEDGE = "frontend_update_existing_knowledge"
ASSIGN_KNOWLEDGE = "frontend_assign_knowledge"
DISMISS_KNOWLEDGE = "frontend_dismiss_knowledge"

# block_id prefixes so update handlers can find and strip just these blocks
# from a card without disturbing any other content on the message.
KNOWLEDGE_ACTIONS_BLOCK_PREFIX = "frontend_knowledge_actions"
KNOWLEDGE_ASSIGN_BLOCK_PREFIX = "frontend_knowledge_assign"

_MAX_DRAFT_PREVIEW_CHARS = 2900


def _task_value(task_id: int) -> str:
    return json.dumps({"task_id": task_id})


def build_knowledge_task_blocks(task: dict) -> list[dict]:
    """Render one AI-drafted knowledge article as a reviewable, actionable card.

    Replaces the old raw symptom/resolution text hand-off: the technician
    reviewing this in #create-knowledge sees a ready-to-publish article, a
    duplicate-knowledge flag when a related article already exists, and one
    click each to publish, update the existing article instead, assign a
    reviewer, or dismiss.
    """
    contributors = ", ".join(f"<@{c}>" for c in (task.get("contributors") or [])) or "not yet attributed"
    markdown_preview = (task.get("drafted_markdown") or "").strip()
    if len(markdown_preview) > _MAX_DRAFT_PREVIEW_CHARS:
        markdown_preview = markdown_preview[:_MAX_DRAFT_PREVIEW_CHARS].rstrip() + "\n…"

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Knowledge task {task['task_id']} — AI-drafted article ready for review*\n"
                    f"*Incident:* `{task.get('incident_number') or 'n/a'}`   "
                    f"*Contributors:* {contributors}"
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": markdown_preview or "_No draft available._"},
        },
    ]

    if task.get("related_document_id"):
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":link: This looks related to existing knowledge: "
                        f"*{task.get('related_document_title') or task['related_document_id']}*. "
                        "Review it before publishing a new article so this becomes an update, "
                        "not a duplicate."
                    ),
                },
            }
        )

    actions_elements = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Publish new article"},
            "style": "primary",
            "action_id": PUBLISH_KNOWLEDGE,
            "value": _task_value(task["id"]),
        }
    ]
    if task.get("related_document_id"):
        actions_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Update existing article instead"},
                "action_id": UPDATE_EXISTING_KNOWLEDGE,
                "value": _task_value(task["id"]),
            }
        )
    actions_elements.append(
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Dismiss"},
            "style": "danger",
            "action_id": DISMISS_KNOWLEDGE,
            "value": _task_value(task["id"]),
        }
    )
    blocks.append(
        {
            "type": "actions",
            "block_id": f"{KNOWLEDGE_ACTIONS_BLOCK_PREFIX}:{task['id']}",
            "elements": actions_elements,
        }
    )
    blocks.append(
        {
            "type": "actions",
            "block_id": f"{KNOWLEDGE_ASSIGN_BLOCK_PREFIX}:{task['id']}",
            "elements": [
                {
                    "type": "users_select",
                    "action_id": ASSIGN_KNOWLEDGE,
                    "placeholder": {"type": "plain_text", "text": "Assign a teammate to review"},
                }
            ],
        }
    )
    return blocks


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
        {
            "type": "actions",
            "block_id": "frontend_resolution_capture",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Capture knowledge"},
                    "style": "primary",
                    "action_id": CAPTURE_RESOLUTION,
                    "value": value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Not now"},
                    "action_id": DISMISS_RESOLUTION,
                    "value": value,
                },
            ],
        },
    ]

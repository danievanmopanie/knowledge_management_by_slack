"""Block Kit rendering for governed revisions of existing knowledge articles."""

from __future__ import annotations

from src.bot.knowledge_governance_interactivity import build_governance_blocks

_MAX_NOTE_CHARS = 1200
_MAX_PREVIEW_CHARS = 1800


def build_knowledge_edit_card(
    task: dict,
    *,
    owner_user_id: str | None = None,
    pending_reviews: list[dict] | None = None,
) -> list[dict]:
    """Render one persistent review card for a staged article revision.

    The card appears while drafting is still in progress. Governance controls are
    available immediately and stay attached when the same message is refreshed
    with the completed draft.
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

    blocks.extend(
        build_governance_blocks(
            document_id=str(task["document_id"]),
            owner_user_id=owner_user_id,
            pending_reviews=pending_reviews or [],
        )
    )
    return blocks

"""Background Atlas drafting for existing-article revision tasks.

Slack acknowledgement/card creation happens before this module is scheduled. The
job updates the same shared message when the draft succeeds or fails.
"""

from __future__ import annotations

import asyncio
import logging

from src.bot.frontend_edit_actions import build_knowledge_edit_card
from src.bot.knowledge_governance_interactivity import get_store as get_governance_store
from src.knowledge.article_revision import (
    apply_review_feedback,
    reconstruct_document_text,
    revise_article,
)
from src.knowledge.edit_requests import EditRequestStore

logger = logging.getLogger(__name__)

_edit_store: EditRequestStore | None = None


def get_edit_store() -> EditRequestStore:
    global _edit_store
    if _edit_store is None:
        _edit_store = EditRequestStore()
    return _edit_store


async def render_revision_task(client, task: dict) -> None:
    """Refresh the one persistent Slack card from durable task/governance state."""
    channel_id = str(task.get("shared_channel_id") or "")
    message_ts = str(task.get("shared_message_ts") or "")
    if not channel_id or not message_ts:
        return

    governance = get_governance_store()
    document_id = str(task["document_id"])
    version_id = str(task.get("base_version_id") or "") or None
    owner = governance.get_owner(document_id)
    pending = governance.pending_reviews_for_article(document_id, version_id=version_id)
    completed = governance.completed_reviews_for_article(document_id, version_id=version_id, limit=10)
    current_text = ""
    if str(task.get("status") or "") == "review":
        try:
            current_text = reconstruct_document_text(document_id)
        except Exception:
            logger.exception("Could not reconstruct current article for revision diff %s", task.get("request_id"))

    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=f"Knowledge edit {task['request_id']} for {task['document_title']}",
        blocks=build_knowledge_edit_card(
            task,
            owner_user_id=str((owner or {}).get("owner_user_id") or "") or None,
            pending_reviews=pending,
            completed_reviews=completed,
            current_text=current_text,
        ),
    )


async def draft_revision_task(client, request_id: int) -> None:
    """Draft one persisted revision task and refresh its existing Slack card."""
    store = get_edit_store()
    task = store.get(request_id)
    if task is None or task.get("status") != "drafting":
        return

    try:
        current_text = reconstruct_document_text(str(task["document_id"]))
        proposed = await revise_article(
            current_text=current_text,
            edit_note=str(task.get("edit_note") or ""),
        )
        store.set_draft(request_id, proposed)
    except Exception as exc:
        logger.exception("Knowledge edit %s drafting failed", task.get("request_id"))
        store.mark_failed(request_id, error_message=str(exc))

    updated = store.get(request_id)
    if updated is not None:
        await render_revision_task(client, updated)


async def apply_feedback_task(client, request_id: int) -> None:
    """Apply only the completed review inputs explicitly selected by the owner action."""
    store = get_edit_store()
    task = store.get(request_id)
    if task is None or task.get("status") != "feedback_drafting":
        return

    review_ids = {int(item) for item in task.get("feedback_review_ids") or []}
    governance = get_governance_store()
    completed = governance.completed_reviews_for_article(
        str(task["document_id"]),
        version_id=str(task.get("base_version_id") or "") or None,
        limit=20,
    )
    feedback = [item for item in completed if int(item.get("id") or 0) in review_ids]

    try:
        revised = await apply_review_feedback(
            proposed_text=str(task.get("proposed_text") or ""),
            reviewer_feedback=feedback,
        )
        store.set_feedback_draft(request_id, revised)
    except Exception as exc:
        logger.exception("Knowledge edit %s review-feedback drafting failed", task.get("request_id"))
        store.mark_failed(request_id, error_message=str(exc))

    updated = store.get(request_id)
    if updated is not None:
        await render_revision_task(client, updated)


def schedule_revision_draft(client, request_id: int) -> asyncio.Task:
    """Schedule expensive drafting after the Slack action has already acknowledged."""
    return asyncio.create_task(draft_revision_task(client, request_id))


def schedule_feedback_draft(client, request_id: int) -> asyncio.Task:
    """Schedule Atlas only after the owner explicitly chooses Apply review feedback."""
    return asyncio.create_task(apply_feedback_task(client, request_id))

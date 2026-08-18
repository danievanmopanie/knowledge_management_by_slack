"""Deterministic review decisions for existing-article revision tasks."""

from __future__ import annotations

import json
import logging

from slack_bolt.async_app import AsyncApp

from src.bot.frontend_edit_actions import (
    APPLY_REVIEW_FEEDBACK,
    APPROVE_KNOWLEDGE_EDIT,
    DISMISS_KNOWLEDGE_EDIT,
    VIEW_FULL_KNOWLEDGE_DRAFT,
)
from src.bot.knowledge_edit_drafting import (
    get_edit_store,
    render_revision_task,
    schedule_feedback_draft,
)
from src.bot.knowledge_governance_interactivity import get_store as get_governance_store
from src.knowledge.catalog import KnowledgeCatalog, StaleKnowledgeVersionError
from src.knowledge.governed_ingest import commit_knowledge

logger = logging.getLogger(__name__)

_catalog: KnowledgeCatalog | None = None
_MODAL_CHUNK = 2800
_MODAL_MAX_CHUNKS = 35


def get_catalog() -> KnowledgeCatalog:
    global _catalog
    if _catalog is None:
        _catalog = KnowledgeCatalog()
    return _catalog


def _request_id(body: dict) -> int | None:
    try:
        return int(json.loads(body["actions"][0]["value"])["request_id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _publishing_authority(task: dict) -> str:
    owner = get_governance_store().get_owner(str(task["document_id"]))
    return str((owner or {}).get("owner_user_id") or task.get("requested_by") or "")


def build_full_draft_modal(task: dict) -> dict:
    """Render the proposed article read-only without filling the shared channel."""
    proposed = str(task.get("proposed_text") or "").strip() or "Draft unavailable."
    chunks = [proposed[i : i + _MODAL_CHUNK] for i in range(0, len(proposed), _MODAL_CHUNK)]
    truncated = len(chunks) > _MODAL_MAX_CHUNKS
    chunks = chunks[:_MODAL_MAX_CHUNKS]
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{task['document_title']}*\nProposed governed revision `{task['request_id']}`",
            },
        },
        {"type": "divider"},
    ]
    for index, chunk in enumerate(chunks, 1):
        blocks.append(
            {
                "type": "section",
                "block_id": f"full_draft:{index}",
                "text": {"type": "mrkdwn", "text": chunk},
            }
        )
    if truncated:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "_This draft exceeds Slack's safe modal size; the shared change preview remains complete for the changed lines shown._",
                    }
                ],
            }
        )
    return {
        "type": "modal",
        "callback_id": "knowledge_edit_full_draft_modal",
        "title": {"type": "plain_text", "text": "Full draft"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": blocks,
    }


async def _not_authorised(client, body: dict, authority_id: str) -> None:
    channel_id = str((body.get("channel") or {}).get("id") or "")
    actor_id = str((body.get("user") or {}).get("id") or "")
    if channel_id and actor_id:
        await client.chat_postEphemeral(
            channel=channel_id,
            user=actor_id,
            text=(
                f"Only <@{authority_id}> can make the final publish/dismiss decision for this revision."
                if authority_id
                else "This revision does not currently have a publishing authority."
            ),
        )


def register(app: AsyncApp) -> None:
    @app.action(VIEW_FULL_KNOWLEDGE_DRAFT)
    async def view_full_draft(ack, body, client):
        await ack()
        request_id = _request_id(body)
        if request_id is None:
            return
        task = get_edit_store().get(request_id)
        if task is None or task.get("status") != "review":
            return
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=build_full_draft_modal(task),
        )

    @app.action(APPLY_REVIEW_FEEDBACK)
    async def apply_feedback(ack, body, client):
        await ack()
        request_id = _request_id(body)
        if request_id is None:
            return

        store = get_edit_store()
        task = store.get(request_id)
        if task is None or task.get("status") != "review":
            return

        actor_id = str((body.get("user") or {}).get("id") or "")
        authority_id = _publishing_authority(task)
        if not actor_id or actor_id != authority_id:
            await _not_authorised(client, body, authority_id)
            return

        active = get_catalog().active_version(str(task["document_id"]))
        active_version_id = str((active or {}).get("version_id") or "")
        if active_version_id != str(task.get("base_version_id") or ""):
            store.mark_stale(request_id, decided_by=actor_id)
            stale = store.get(request_id)
            if stale is not None:
                await render_revision_task(client, stale)
            return

        governance = get_governance_store()
        completed = governance.completed_reviews_for_article(
            str(task["document_id"]),
            version_id=str(task.get("base_version_id") or "") or None,
            limit=20,
        )
        applied = {int(item) for item in task.get("applied_review_ids") or []}
        review_ids = [
            int(item["id"])
            for item in completed
            if int(item.get("id") or 0) not in applied and str(item.get("response_note") or "").strip()
        ]
        if not review_ids:
            channel_id = str((body.get("channel") or {}).get("id") or "")
            if channel_id:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=actor_id,
                    text="There is no new completed technical review feedback to apply.",
                )
            return

        if not store.start_feedback_draft(request_id, review_ids=review_ids):
            return
        drafting = store.get(request_id)
        if drafting is not None:
            await render_revision_task(client, drafting)
        schedule_feedback_draft(client, request_id)

    @app.action(APPROVE_KNOWLEDGE_EDIT)
    async def approve_publish(ack, body, client):
        await ack()
        request_id = _request_id(body)
        if request_id is None:
            return

        store = get_edit_store()
        task = store.get(request_id)
        if task is None or task.get("status") != "review":
            return

        actor_id = str((body.get("user") or {}).get("id") or "")
        authority_id = _publishing_authority(task)
        if not actor_id or actor_id != authority_id:
            await _not_authorised(client, body, authority_id)
            return

        catalog = get_catalog()
        document = catalog.get_document(str(task["document_id"]))
        if document is None:
            logger.error("Knowledge edit %s references missing document %s", task["request_id"], task["document_id"])
            return

        owner = get_governance_store().get_owner(str(task["document_id"]))
        owner_id = str((owner or {}).get("owner_user_id") or document.get("owner_id") or "") or None

        try:
            result = commit_knowledge(
                text=str(task.get("proposed_text") or ""),
                title=str(task["document_title"]),
                source_id=str(document["source_id"]),
                source_system=str(document["source_system"]),
                owner_id=owner_id,
                visibility=str(document.get("visibility") or "internal"),
                expected_version_id=str(task["base_version_id"]),
            )
        except StaleKnowledgeVersionError:
            store.mark_stale(request_id, decided_by=actor_id)
            stale = store.get(request_id)
            if stale is not None:
                await render_revision_task(client, stale)
            return
        except Exception:
            logger.exception("Publishing knowledge edit %s failed", task["request_id"])
            channel_id = str((body.get("channel") or {}).get("id") or "")
            if channel_id:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=actor_id,
                    text="Publishing failed. The governed article was not intentionally advanced; please retry after checking the service logs.",
                )
            return

        store.mark_published(
            request_id,
            published_document_id=str(result["document_id"]),
            published_version_id=str(result["version_id"]),
            decided_by=actor_id,
        )
        published = store.get(request_id)
        if published is not None:
            await render_revision_task(client, published)

    @app.action(DISMISS_KNOWLEDGE_EDIT)
    async def dismiss_revision(ack, body, client):
        await ack()
        request_id = _request_id(body)
        if request_id is None:
            return

        store = get_edit_store()
        task = store.get(request_id)
        if task is None or task.get("status") not in {"review", "draft_failed", "stale"}:
            return

        actor_id = str((body.get("user") or {}).get("id") or "")
        authority_id = _publishing_authority(task)
        if not actor_id or actor_id != authority_id:
            await _not_authorised(client, body, authority_id)
            return

        store.dismiss(request_id, decided_by=actor_id)
        dismissed = store.get(request_id)
        if dismissed is not None:
            await render_revision_task(client, dismissed)

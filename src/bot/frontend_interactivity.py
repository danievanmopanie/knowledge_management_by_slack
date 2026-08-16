"""Slack interactivity for frontend-support knowledge capture and knowledge-gap creation."""

from __future__ import annotations

import json
import logging

from slack_bolt.async_app import AsyncApp

from src.agents.frontend_support.collaboration import FrontendCollaborationService, MessageKind
from src.bot.frontend_actions import (
    ASSIGN_KNOWLEDGE,
    CAPTURE_RESOLUTION,
    DISMISS_KNOWLEDGE,
    DISMISS_RESOLUTION,
    KNOWLEDGE_ACTIONS_BLOCK_PREFIX,
    KNOWLEDGE_ASSIGN_BLOCK_PREFIX,
    PUBLISH_KNOWLEDGE,
    UPDATE_EXISTING_KNOWLEDGE,
    build_knowledge_task_blocks,
)
from src.bot.notify import send_dm
from src.core.config import settings
from src.core.context import RequestContext
from src.knowledge.article_drafting import draft_knowledge_article
from src.knowledge.catalog import KnowledgeCatalog
from src.knowledge.governed_ingest import commit_knowledge
from src.knowledge.retrieval_models import RetrievalQuery
from src.knowledge.retriever import HybridRetriever

logger = logging.getLogger(__name__)

_service: FrontendCollaborationService | None = None
_retriever: HybridRetriever | None = None


def get_service() -> FrontendCollaborationService:
    global _service
    if _service is None:
        _service = FrontendCollaborationService()
    return _service


def get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever


def _remove_capture_controls(blocks: list[dict]) -> list[dict]:
    return [
        block
        for block in blocks
        if block.get("block_id") != "frontend_resolution_capture"
    ]


def _strip_block_prefix(blocks: list[dict], prefix: str) -> list[dict]:
    return [block for block in blocks if not str(block.get("block_id", "")).startswith(prefix)]


async def _update_knowledge_card(client, body: dict, *, status_line: str, remove_actions: bool) -> None:
    """Update a knowledge-task card in place with a status line instead of a new message."""
    message = body.get("message") or {}
    channel_id = (body.get("channel") or {}).get("id")
    message_ts = message.get("ts")
    if not channel_id or not message_ts:
        return
    blocks = list(message.get("blocks") or [])
    if remove_actions:
        blocks = _strip_block_prefix(blocks, KNOWLEDGE_ACTIONS_BLOCK_PREFIX)
        blocks = _strip_block_prefix(blocks, KNOWLEDGE_ASSIGN_BLOCK_PREFIX)
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": status_line}]})
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=message.get("text", status_line),
        blocks=blocks,
    )


async def _propose_knowledge_task(
    *,
    client,
    channel_id: str,
    thread_ts: str,
    actor_id: str,
) -> None:
    """Turn a confirmed field fix into a ready-to-publish, AI-drafted knowledge article.

    This is the simplified create-knowledge flow: instead of handing a reviewer raw
    symptom/resolution text to rewrite from scratch, it always produces a structured
    draft article, checks governed knowledge for a likely duplicate first (strong
    match = skip creating anything and point at the existing article; weak match =
    flag it and offer "update existing" alongside "publish new"), and posts one
    Block Kit card to #create-knowledge with publish/update/assign/dismiss controls.
    """
    service = get_service()
    state = service.store.get_thread(channel_id, thread_ts)
    events = service.store.recent_events(channel_id, thread_ts, limit=50)
    resolution = service.store._resolution_summary(events)
    symptom = state.root_message
    query = "\n".join(part for part in (symptom, resolution) if part).strip()
    if not query:
        return

    context = RequestContext.from_slack(channel_id=channel_id, user_id=actor_id, thread_ts=thread_ts)
    result = None
    try:
        result = get_retriever().search(
            RetrievalQuery(text=query, context=context, limit=3, graph_depth=0)
        )
    except Exception:
        logger.exception("Governed duplicate-knowledge check failed for %s/%s", channel_id, thread_ts)

    if result is not None and result.evidence_level == "strong" and result.candidates:
        top_source = result.candidates[0].metadata.get("source", "existing knowledge")
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=(
                f"This resolution matches existing formal knowledge (*{top_source}*), so I won't create "
                "a duplicate article. The field fix stays recorded as confirmed operational evidence."
            ),
        )
        return

    related_document_id = None
    related_document_title = None
    if result is not None and result.evidence_level == "weak" and result.candidates:
        related_document_id = result.candidates[0].metadata.get("document_id")
        related_document_title = result.candidates[0].metadata.get("source")

    contributors = sorted({e["user_id"] for e in events})
    actions_tried = "; ".join(
        e["text"] for e in events if e["message_kind"] == MessageKind.TROUBLESHOOTING.value
    )
    drafted = await draft_knowledge_article(
        symptom=symptom,
        resolution=resolution,
        actions_tried=actions_tried,
        contributors=contributors,
        related_incidents=[state.incident_number] if state.incident_number else [],
        fallback_title=f"Resolution for {state.incident_number or 'field incident'}",
    )

    try:
        task = service.create_knowledge_gap_task(
            channel_id,
            thread_ts,
            actor_id,
            "Confirmed field resolution drafted into a formal knowledge article for review.",
            related_document_id=related_document_id,
            related_document_title=related_document_title,
            drafted_title=drafted.title,
            drafted_markdown=drafted.markdown,
        )
    except Exception:
        logger.exception("Could not create knowledge task for %s/%s", channel_id, thread_ts)
        return

    target_channel = settings.channel_create_knowledge or settings.channel_knowledge_uploads
    if not target_channel:
        logger.warning(
            "Knowledge task %s created but no create-knowledge channel is configured",
            task["task_id"],
        )
        return

    await client.chat_postMessage(
        channel=target_channel,
        text=f"Knowledge task {task['task_id']} — AI-drafted article ready for review",
        blocks=build_knowledge_task_blocks(task),
    )
    await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=(
            f"Thanks — I drafted a knowledge article from this fix (*{task['task_id']}*) for review in "
            f"<#{target_channel}>."
        ),
    )


def register(app: AsyncApp) -> None:
    @app.action(CAPTURE_RESOLUTION)
    async def capture_resolution(ack, body, client):
        await ack()
        action = body["actions"][0]
        payload = json.loads(action["value"])
        channel_id = payload["channel_id"]
        thread_ts = payload["thread_ts"]
        actor_id = body["user"]["id"]
        result = get_service().confirm_knowledge(channel_id, thread_ts, actor_id)

        message = body.get("message") or {}
        message_ts = message.get("ts")
        captured = result.startswith("Captured this resolution")
        if message_ts and captured:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=message.get("text", "Knowledge capture confirmed."),
                blocks=_remove_capture_controls(message.get("blocks", [])),
            )
        await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=result)

        if captured:
            await _propose_knowledge_task(
                client=client, channel_id=channel_id, thread_ts=thread_ts, actor_id=actor_id
            )

    @app.action(DISMISS_RESOLUTION)
    async def dismiss_resolution(ack, body, client):
        await ack()
        message = body.get("message") or {}
        channel_id = body.get("channel", {}).get("id")
        message_ts = message.get("ts")
        if channel_id and message_ts:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=message.get("text", "Knowledge capture dismissed."),
                blocks=_remove_capture_controls(message.get("blocks", [])),
            )

    @app.action(PUBLISH_KNOWLEDGE)
    async def publish_knowledge(ack, body, client):
        await ack()
        task_id = json.loads(body["actions"][0]["value"])["task_id"]
        service = get_service()
        task = service.store.get_knowledge_task(task_id)
        if not task or task["status"] == "published":
            return
        result = commit_knowledge(
            text=task["drafted_markdown"] or task["resolution"],
            title=task["drafted_title"] or f"Knowledge task {task['task_id']}",
            source_id=f"knowledge-task-{task['id']}",
            owner_id=body["user"]["id"],
        )
        service.store.mark_knowledge_task_published(task["id"], published_document_id=result["document_id"])
        await _update_knowledge_card(
            client,
            body,
            status_line=(
                f":white_check_mark: Published by <@{body['user']['id']}> → "
                f"{result['chunks']} searchable chunk(s)."
            ),
            remove_actions=True,
        )

    @app.action(UPDATE_EXISTING_KNOWLEDGE)
    async def update_existing_knowledge(ack, body, client):
        await ack()
        task_id = json.loads(body["actions"][0]["value"])["task_id"]
        service = get_service()
        task = service.store.get_knowledge_task(task_id)
        if not task or not task.get("related_document_id"):
            return
        doc = KnowledgeCatalog().get_document(task["related_document_id"])
        if not doc:
            channel_id = (body.get("channel") or {}).get("id")
            thread_ts = (body.get("message") or {}).get("ts")
            if channel_id:
                await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="I could not find the related article anymore; use *Publish new article* instead.",
                )
            return
        result = commit_knowledge(
            text=task["drafted_markdown"] or task["resolution"],
            title=task["drafted_title"] or doc["title"],
            source_id=doc["source_id"],
            owner_id=body["user"]["id"],
        )
        service.store.mark_knowledge_task_published(task["id"], published_document_id=result["document_id"])
        await _update_knowledge_card(
            client,
            body,
            status_line=(
                f":white_check_mark: Existing article *{doc['title']}* updated by "
                f"<@{body['user']['id']}> → {result['chunks']} searchable chunk(s)."
            ),
            remove_actions=True,
        )

    @app.action(ASSIGN_KNOWLEDGE)
    async def assign_knowledge(ack, body, client):
        await ack()
        block_id = str(body["actions"][0].get("block_id") or "")
        try:
            task_id = int(block_id.split(":", 1)[1])
        except (IndexError, ValueError):
            return
        assignee_id = body["actions"][0].get("selected_user")
        if not assignee_id:
            return
        service = get_service()
        task = service.store.get_knowledge_task(task_id)
        if not task:
            return
        service.store.assign_knowledge_task(task_id, assignee_id)

        review_channel = settings.channel_create_knowledge or settings.channel_knowledge_uploads
        await send_dm(
            client,
            assignee_id,
            (
                f"You were assigned knowledge task *{task['task_id']}* by <@{body['user']['id']}>.\n"
                f"*Problem:* {task['symptom'] or '(not captured)'}\n"
                f"*Confirmed resolution:* {task['resolution']}\n"
                + (
                    f"Review the AI-drafted article and publish it in <#{review_channel}>."
                    if review_channel
                    else "Review the AI-drafted article and publish it."
                )
            ),
        )
        await _update_knowledge_card(
            client,
            body,
            status_line=f":bust_in_silhouette: Assigned to <@{assignee_id}> by <@{body['user']['id']}>.",
            remove_actions=False,
        )

    @app.action(DISMISS_KNOWLEDGE)
    async def dismiss_knowledge(ack, body, client):
        await ack()
        task_id = json.loads(body["actions"][0]["value"])["task_id"]
        get_service().store.dismiss_knowledge_task(task_id)
        await _update_knowledge_card(
            client,
            body,
            status_line=f":no_entry_sign: Dismissed by <@{body['user']['id']}>.",
            remove_actions=True,
        )

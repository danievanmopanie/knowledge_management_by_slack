"""Slack interactivity for flagging and revising an existing knowledge article from #frontend-support."""

from __future__ import annotations

import json
import logging

from slack_bolt.async_app import AsyncApp

from src.bot.frontend_edit_actions import (
    APPROVE_KNOWLEDGE_EDIT,
    ASSIGN_KNOWLEDGE_EDIT,
    DISMISS_KNOWLEDGE_EDIT,
    DISMISS_KNOWLEDGE_EDIT_PROMPT,
    KNOWLEDGE_EDIT_ACTIONS_BLOCK_PREFIX,
    KNOWLEDGE_EDIT_ASSIGN_BLOCK_PREFIX,
    KNOWLEDGE_EDIT_PROMPT_BLOCK_ID,
    PROPOSE_KNOWLEDGE_EDIT,
    build_edit_prompt_blocks,
    build_knowledge_edit_review_blocks,
)
from src.bot.notify import send_dm
from src.core.config import settings
from src.knowledge.article_revision import reconstruct_document_text, revise_article
from src.knowledge.catalog import KnowledgeCatalog
from src.knowledge.citation_memory import CitationMemory
from src.knowledge.edit_requests import EditRequestStore
from src.knowledge.governed_ingest import commit_knowledge

logger = logging.getLogger(__name__)

_citations: CitationMemory | None = None
_edit_requests: EditRequestStore | None = None


def get_citation_memory() -> CitationMemory:
    global _citations
    if _citations is None:
        _citations = CitationMemory()
    return _citations


def get_edit_request_store() -> EditRequestStore:
    global _edit_requests
    if _edit_requests is None:
        _edit_requests = EditRequestStore()
    return _edit_requests


async def propose_knowledge_edit_prompt(*, client, channel_id: str, thread_ts: str, edit_note: str) -> None:
    """Ask in-thread before flagging anything, resolving "that article" from citation memory.

    Called when `FrontendCollaborationService.observe()` classifies a message as a
    knowledge-edit request. If nothing was recently cited in this thread, the
    technician is asked to surface the article first rather than guessing.
    """
    cited = get_citation_memory().get(channel_id, thread_ts)
    if not cited:
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=(
                "I'd like to flag a knowledge article for review, but I don't have one cited in this "
                "thread yet. Ask the question that surfaces it first (or name the article) and I can "
                "pick this up from there."
            ),
        )
        return

    await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=f"Want me to flag {cited['title']} for review with this note?",
        blocks=build_edit_prompt_blocks(
            channel_id=channel_id,
            thread_ts=thread_ts,
            document_title=cited["title"],
            edit_note=edit_note,
        ),
    )


def _strip_block_prefix(blocks: list[dict], prefix: str) -> list[dict]:
    return [block for block in blocks if not str(block.get("block_id", "")).startswith(prefix)]


def _strip_block_id(blocks: list[dict], block_id: str) -> list[dict]:
    return [block for block in blocks if block.get("block_id") != block_id]


async def _update_card(client, body: dict, *, status_line: str, remove_actions: bool) -> None:
    message = body.get("message") or {}
    channel_id = (body.get("channel") or {}).get("id")
    message_ts = message.get("ts")
    if not channel_id or not message_ts:
        return
    blocks = list(message.get("blocks") or [])
    if remove_actions:
        blocks = _strip_block_prefix(blocks, KNOWLEDGE_EDIT_ACTIONS_BLOCK_PREFIX)
        blocks = _strip_block_prefix(blocks, KNOWLEDGE_EDIT_ASSIGN_BLOCK_PREFIX)
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": status_line}]})
    await client.chat_update(
        channel=channel_id, ts=message_ts, text=message.get("text", status_line), blocks=blocks
    )


def register(app: AsyncApp) -> None:
    @app.action(PROPOSE_KNOWLEDGE_EDIT)
    async def propose_knowledge_edit(ack, body, client):
        await ack()
        payload = json.loads(body["actions"][0]["value"])
        channel_id = payload["channel_id"]
        thread_ts = payload["thread_ts"]
        edit_note = payload.get("edit_note", "")
        actor_id = body["user"]["id"]

        cited = get_citation_memory().get(channel_id, thread_ts)
        message = body.get("message") or {}
        if not cited:
            message_ts = message.get("ts")
            if message_ts:
                await client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    text="I lost track of which article this was for; nothing was flagged.",
                    blocks=_strip_block_id(message.get("blocks", []), KNOWLEDGE_EDIT_PROMPT_BLOCK_ID),
                )
            return

        catalog = KnowledgeCatalog()
        doc = catalog.get_document(cited["document_id"])
        current_text = reconstruct_document_text(cited["document_id"])
        proposed_text = await revise_article(current_text=current_text, edit_note=edit_note)

        task = get_edit_request_store().create(
            channel_id=channel_id,
            thread_ts=thread_ts,
            document_id=cited["document_id"],
            document_title=(doc or {}).get("title") or cited["title"],
            edit_note=edit_note,
            proposed_text=proposed_text,
            requested_by=actor_id,
        )

        message_ts = message.get("ts")
        if message_ts:
            blocks = _strip_block_id(message.get("blocks", []), KNOWLEDGE_EDIT_PROMPT_BLOCK_ID)
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": f":white_check_mark: Flagged as {task['request_id']}."}],
                }
            )
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=message.get("text", "Knowledge edit flagged."),
                blocks=blocks,
            )

        target_channel = settings.channel_create_knowledge or settings.channel_knowledge_uploads
        if not target_channel:
            logger.warning(
                "Knowledge edit %s flagged but no create-knowledge channel is configured", task["request_id"]
            )
            return
        await client.chat_postMessage(
            channel=target_channel,
            text=f"Knowledge edit {task['request_id']} ready for review",
            blocks=build_knowledge_edit_review_blocks(task),
        )

    @app.action(DISMISS_KNOWLEDGE_EDIT_PROMPT)
    async def dismiss_knowledge_edit_prompt(ack, body, client):
        await ack()
        message = body.get("message") or {}
        channel_id = (body.get("channel") or {}).get("id")
        message_ts = message.get("ts")
        if channel_id and message_ts:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=message.get("text", "Not flagging that article."),
                blocks=_strip_block_id(message.get("blocks", []), KNOWLEDGE_EDIT_PROMPT_BLOCK_ID),
            )

    @app.action(APPROVE_KNOWLEDGE_EDIT)
    async def approve_knowledge_edit(ack, body, client):
        await ack()
        request_id = json.loads(body["actions"][0]["value"])["request_id"]
        store = get_edit_request_store()
        task = store.get(request_id)
        if not task or task["status"] == "published":
            return
        catalog = KnowledgeCatalog()
        doc = catalog.get_document(task["document_id"])
        source_id = (doc or {}).get("source_id") or task["document_id"]
        result = commit_knowledge(
            text=task["proposed_text"],
            title=task["document_title"],
            source_id=source_id,
            owner_id=body["user"]["id"],
        )
        store.mark_published(task["id"], published_document_id=result["document_id"])
        await _update_card(
            client,
            body,
            status_line=(
                f":white_check_mark: Revision approved by <@{body['user']['id']}> → "
                f"{result['chunks']} searchable chunk(s)."
            ),
            remove_actions=True,
        )

    @app.action(ASSIGN_KNOWLEDGE_EDIT)
    async def assign_knowledge_edit(ack, body, client):
        await ack()
        block_id = str(body["actions"][0].get("block_id") or "")
        try:
            request_id = int(block_id.split(":", 1)[1])
        except (IndexError, ValueError):
            return
        assignee_id = body["actions"][0].get("selected_user")
        if not assignee_id:
            return
        store = get_edit_request_store()
        task = store.get(request_id)
        if not task:
            return
        store.assign(request_id, assignee_id)

        review_channel = settings.channel_create_knowledge or settings.channel_knowledge_uploads
        await send_dm(
            client,
            assignee_id,
            (
                f"You were assigned knowledge edit *{task['request_id']}* by <@{body['user']['id']}>.\n"
                f"*Article:* {task['document_title']}\n"
                f"*Note:* {task['edit_note']}\n"
                + (
                    f"Review the drafted revision and approve it in <#{review_channel}>."
                    if review_channel
                    else "Review the drafted revision and approve it."
                )
            ),
        )
        await _update_card(
            client,
            body,
            status_line=f":bust_in_silhouette: Assigned to <@{assignee_id}> by <@{body['user']['id']}>.",
            remove_actions=False,
        )

    @app.action(DISMISS_KNOWLEDGE_EDIT)
    async def dismiss_knowledge_edit(ack, body, client):
        await ack()
        request_id = json.loads(body["actions"][0]["value"])["request_id"]
        get_edit_request_store().dismiss(request_id)
        await _update_card(
            client,
            body,
            status_line=f":no_entry_sign: Dismissed by <@{body['user']['id']}>.",
            remove_actions=True,
        )

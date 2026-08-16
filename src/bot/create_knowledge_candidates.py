"""Slack handlers and outbox pump for Knowledge Candidate review/publication."""

from __future__ import annotations

import asyncio
import json
import logging

from src.bot.blockkit import ids
from src.bot.knowledge_candidate_blocks import (
    candidate_card,
    edit_candidate_modal,
    request_changes_modal,
    request_input_modal,
)
from src.core.config import settings
from src.knowledge.knowledge_candidates import KnowledgeCandidate, KnowledgeCandidateStore

logger = logging.getLogger(__name__)
OUTBOX_POLL_SECONDS = 2.0


def _candidate_id(body: dict) -> str:
    return str(((body.get("actions") or [{}])[0]).get("value") or "").strip().upper()


def _message_context(body: dict) -> tuple[str, str]:
    message = body.get("message") or {}
    container = body.get("container") or {}
    channel_id = str((body.get("channel") or {}).get("id") or container.get("channel_id") or "")
    message_ts = str(message.get("ts") or container.get("message_ts") or "")
    return channel_id, message_ts


def _view_value(view: dict, block_id: str) -> str:
    state = ((view.get("state") or {}).get("values") or {}).get(block_id) or {}
    item = state.get("value") or next(iter(state.values()), {})
    return str(item.get("value") or "").strip()


def _view_user(view: dict, block_id: str) -> str:
    state = ((view.get("state") or {}).get("values") or {}).get(block_id) or {}
    item = state.get("value") or next(iter(state.values()), {})
    return str(item.get("selected_user") or "").strip()


def _metadata(view: dict) -> dict:
    try:
        return json.loads(view.get("private_metadata") or "{}")
    except (TypeError, ValueError):
        return {}


async def _update_card(client, candidate: KnowledgeCandidate, *, channel_id: str, message_ts: str) -> None:
    if not channel_id or not message_ts:
        return
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=f"{candidate.candidate_id}: {candidate.title} — {candidate.status}",
        blocks=candidate_card(candidate),
    )


async def _ephemeral(client, body: dict, text: str) -> None:
    channel_id, _ = _message_context(body)
    user_id = str((body.get("user") or {}).get("id") or "")
    if channel_id and user_id:
        await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text)


def register_candidate_workflow(app, store: KnowledgeCandidateStore) -> None:
    @app.action(ids.KNOWLEDGE_CANDIDATE_CLAIM)
    async def claim_candidate(ack, body, client):
        await ack()
        candidate_id = _candidate_id(body)
        user_id = str((body.get("user") or {}).get("id") or "")
        try:
            candidate = store.claim(candidate_id, user_id)
            channel_id, message_ts = _message_context(body)
            await _update_card(client, candidate, channel_id=channel_id, message_ts=message_ts)
        except Exception:
            logger.exception("Could not claim Knowledge Candidate %s", candidate_id)
            await _ephemeral(client, body, f"I could not assign `{candidate_id}`. Check the Create Knowledge logs.")

    @app.action(ids.KNOWLEDGE_CANDIDATE_EDIT)
    async def edit_candidate(ack, body, client):
        await ack()
        candidate_id = _candidate_id(body)
        candidate = store.get(candidate_id)
        if candidate is None:
            await _ephemeral(client, body, f"I cannot find `{candidate_id}`.")
            return
        channel_id, message_ts = _message_context(body)
        await client.views_open(
            trigger_id=body.get("trigger_id"),
            view=edit_candidate_modal(candidate, channel_id=channel_id, message_ts=message_ts),
        )

    @app.action(ids.KNOWLEDGE_CANDIDATE_REQUEST_INPUT)
    async def request_input(ack, body, client):
        await ack()
        candidate_id = _candidate_id(body)
        candidate = store.get(candidate_id)
        if candidate is None:
            await _ephemeral(client, body, f"I cannot find `{candidate_id}`.")
            return
        channel_id, message_ts = _message_context(body)
        await client.views_open(
            trigger_id=body.get("trigger_id"),
            view=request_input_modal(candidate, channel_id=channel_id, message_ts=message_ts),
        )

    @app.action(ids.KNOWLEDGE_CANDIDATE_READY)
    async def ready_candidate(ack, body, client):
        await ack()
        candidate_id = _candidate_id(body)
        try:
            candidate = store.mark_ready(candidate_id)
        except ValueError as exc:
            await _ephemeral(client, body, str(exc) + ". Use *Edit / enrich* first.")
            return
        except Exception:
            logger.exception("Could not mark Knowledge Candidate ready %s", candidate_id)
            await _ephemeral(client, body, f"I could not move `{candidate_id}` to review.")
            return
        channel_id, message_ts = _message_context(body)
        await _update_card(client, candidate, channel_id=channel_id, message_ts=message_ts)

    @app.action(ids.KNOWLEDGE_CANDIDATE_APPROVE)
    async def approve_candidate(ack, body, client):
        await ack()
        candidate_id = _candidate_id(body)
        reviewer_id = str((body.get("user") or {}).get("id") or "")
        try:
            candidate, result = store.publish(candidate_id, reviewer_id=reviewer_id)
            channel_id, message_ts = _message_context(body)
            await _update_card(client, candidate, channel_id=channel_id, message_ts=message_ts)
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=message_ts,
                text=(
                    f"✅ <@{reviewer_id}> approved and published `{candidate.candidate_id}` as governed knowledge "
                    f"(`{result.get('document_id')}`)."
                ),
            )
        except Exception as exc:
            logger.exception("Could not publish Knowledge Candidate %s", candidate_id)
            await _ephemeral(client, body, f"I could not publish `{candidate_id}`: {exc}")

    @app.action(ids.KNOWLEDGE_CANDIDATE_REQUEST_CHANGES)
    async def request_changes(ack, body, client):
        await ack()
        candidate_id = _candidate_id(body)
        candidate = store.get(candidate_id)
        if candidate is None:
            await _ephemeral(client, body, f"I cannot find `{candidate_id}`.")
            return
        channel_id, message_ts = _message_context(body)
        await client.views_open(
            trigger_id=body.get("trigger_id"),
            view=request_changes_modal(candidate, channel_id=channel_id, message_ts=message_ts),
        )

    @app.view(ids.MODAL_KNOWLEDGE_CANDIDATE_EDIT)
    async def save_candidate_edit(ack, body, view, client):
        await ack()
        meta = _metadata(view)
        candidate_id = str(meta.get("candidate_id") or "")
        try:
            candidate = store.update_content(
                candidate_id,
                title=_view_value(view, "title"),
                applies_when=_view_value(view, "applies_when"),
                procedure=_view_value(view, "procedure"),
                validation=_view_value(view, "validation"),
                prerequisites=_view_value(view, "prerequisites"),
                recorded_root_cause=_view_value(view, "root_cause"),
                notes=_view_value(view, "notes"),
            )
            if candidate.status in {"ready_for_review", "needs_input", "changes_requested", "needs_enrichment"}:
                candidate = store.set_status(
                    candidate_id,
                    "in_progress",
                    owner_id=candidate.owner_id or str((body.get("user") or {}).get("id") or ""),
                )
            await _update_card(
                client,
                candidate,
                channel_id=str(meta.get("channel_id") or ""),
                message_ts=str(meta.get("message_ts") or ""),
            )
        except Exception:
            logger.exception("Could not save Knowledge Candidate edit %s", candidate_id)

    @app.view(ids.MODAL_KNOWLEDGE_CANDIDATE_INPUT)
    async def save_input_request(ack, body, view, client):
        await ack()
        meta = _metadata(view)
        candidate_id = str(meta.get("candidate_id") or "")
        question = _view_value(view, "question")
        target_user = _view_user(view, "person")
        try:
            candidate = store.request_input(candidate_id, question=question, user_id=target_user)
            channel_id = str(meta.get("channel_id") or "")
            message_ts = str(meta.get("message_ts") or "")
            await _update_card(client, candidate, channel_id=channel_id, message_ts=message_ts)
            target = f"<@{target_user}> " if target_user else ""
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=message_ts,
                text=f"{target}Input requested for `{candidate_id}`: {question}",
            )
        except Exception:
            logger.exception("Could not request input for Knowledge Candidate %s", candidate_id)

    @app.view(ids.MODAL_KNOWLEDGE_CANDIDATE_CHANGES)
    async def save_change_request(ack, body, view, client):
        await ack()
        meta = _metadata(view)
        candidate_id = str(meta.get("candidate_id") or "")
        note = _view_value(view, "changes")
        reviewer_id = str((body.get("user") or {}).get("id") or "")
        try:
            candidate = store.request_changes(candidate_id, note=note, reviewer_id=reviewer_id)
            channel_id = str(meta.get("channel_id") or "")
            message_ts = str(meta.get("message_ts") or "")
            await _update_card(client, candidate, channel_id=channel_id, message_ts=message_ts)
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=message_ts,
                text=f"Changes requested by <@{reviewer_id}> for `{candidate_id}`: {note}",
            )
        except Exception:
            logger.exception("Could not request changes for Knowledge Candidate %s", candidate_id)


async def candidate_outbox_loop(client, store: KnowledgeCandidateStore) -> None:
    """Post Frontend-created candidates using the Create Knowledge app identity."""
    while True:
        try:
            if settings.channel_create_knowledge:
                for candidate in store.list_unposted(limit=20):
                    posted = await client.chat_postMessage(
                        channel=settings.channel_create_knowledge,
                        text=f"{candidate.candidate_id}: {candidate.title} — needs enrichment",
                        blocks=candidate_card(candidate),
                    )
                    message_ts = str(posted.get("ts") or "")
                    if message_ts:
                        store.mark_posted(candidate.candidate_id, message_ts)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Knowledge Candidate outbox pump failed")
        await asyncio.sleep(OUTBOX_POLL_SECONDS)

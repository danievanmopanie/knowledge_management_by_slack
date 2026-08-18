"""Explicit drafting choices for Knowledge Review Card v2.

The card is deliberately fast and deterministic until a human chooses an action.
Atlas drafting starts only after ``Draft with AI``. Manual editing stays entirely
inside Slack and never publishes by itself.
"""

from __future__ import annotations

import json

from slack_bolt.async_app import AsyncApp

from src.bot.frontend_edit_actions import (
    DRAFT_KNOWLEDGE_EDIT_WITH_AI,
    EDIT_KNOWLEDGE_EDIT_MANUALLY,
)
from src.bot.knowledge_edit_drafting import (
    get_edit_store,
    render_revision_task,
    schedule_revision_draft,
)
from src.bot.knowledge_governance_interactivity import get_store as get_governance_store
from src.knowledge.article_revision import reconstruct_document_text
from src.knowledge.catalog import KnowledgeCatalog

MANUAL_EDIT_MODAL = "knowledge_edit_manual_edit_modal"
_MANUAL_BLOCK_PREFIX = "knowledge_edit_manual_part_"
_MANUAL_ACTION_PREFIX = "knowledge_edit_manual_input_"
_CHUNK_SIZE = 2400
_MAX_CHUNKS = 20

_catalog: KnowledgeCatalog | None = None


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


def _authority(task: dict) -> str:
    owner = get_governance_store().get_owner(str(task["document_id"]))
    return str((owner or {}).get("owner_user_id") or task.get("requested_by") or "")


async def _not_authorised(client, body: dict, authority_id: str) -> None:
    channel_id = str((body.get("channel") or {}).get("id") or "")
    actor_id = str((body.get("user") or {}).get("id") or "")
    if channel_id and actor_id:
        await client.chat_postEphemeral(
            channel=channel_id,
            user=actor_id,
            text=(
                f"Only <@{authority_id}> can draft or edit this revision."
                if authority_id
                else "This revision does not currently have an owner or requester authority."
            ),
        )


def _base_is_current(task: dict) -> bool:
    active = get_catalog().active_version(str(task["document_id"]))
    return str((active or {}).get("version_id") or "") == str(task.get("base_version_id") or "")


def _manual_source(task: dict) -> str:
    proposed = str(task.get("proposed_text") or "").strip()
    return proposed or reconstruct_document_text(str(task["document_id"]))


def build_manual_edit_modal(task: dict, text: str) -> dict:
    """Build a Slack-safe editable modal without silently truncating an article."""
    chunks = [text[i : i + _CHUNK_SIZE] for i in range(0, len(text), _CHUNK_SIZE)] or [""]
    if len(chunks) > _MAX_CHUNKS:
        raise ValueError("Article is too large for safe manual editing in one Slack modal")

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{task['document_title']}*\n"
                    "Edit the proposed article below. Saving updates the draft only — it does *not* publish."
                ),
            },
        }
    ]
    for index, chunk in enumerate(chunks):
        blocks.append(
            {
                "type": "input",
                "block_id": f"{_MANUAL_BLOCK_PREFIX}{index}",
                "label": {
                    "type": "plain_text",
                    "text": "Article text" if len(chunks) == 1 else f"Article text · part {index + 1} of {len(chunks)}",
                },
                "element": {
                    "type": "plain_text_input",
                    "action_id": f"{_MANUAL_ACTION_PREFIX}{index}",
                    "multiline": True,
                    "initial_value": chunk,
                    "max_length": 2500,
                },
            }
        )

    return {
        "type": "modal",
        "callback_id": MANUAL_EDIT_MODAL,
        "private_metadata": json.dumps({"request_id": int(task["id"]), "chunks": len(chunks)}),
        "title": {"type": "plain_text", "text": "Edit knowledge"},
        "submit": {"type": "plain_text", "text": "Save draft"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def register(app: AsyncApp) -> None:
    @app.action(DRAFT_KNOWLEDGE_EDIT_WITH_AI)
    async def draft_with_ai(ack, body, client):
        await ack()
        request_id = _request_id(body)
        if request_id is None:
            return
        store = get_edit_store()
        task = store.get(request_id)
        if task is None or str(task.get("status") or "") not in {"awaiting_action", "draft_failed"}:
            return

        actor_id = str((body.get("user") or {}).get("id") or "")
        authority_id = _authority(task)
        if not actor_id or actor_id != authority_id:
            await _not_authorised(client, body, authority_id)
            return
        if not _base_is_current(task):
            store.mark_stale(request_id, decided_by=actor_id)
            stale = store.get(request_id)
            if stale is not None:
                await render_revision_task(client, stale)
            return
        if not store.start_ai_draft(request_id):
            return
        drafting = store.get(request_id)
        if drafting is not None:
            await render_revision_task(client, drafting)
        schedule_revision_draft(client, request_id)

    @app.action(EDIT_KNOWLEDGE_EDIT_MANUALLY)
    async def edit_manually(ack, body, client):
        await ack()
        request_id = _request_id(body)
        if request_id is None:
            return
        store = get_edit_store()
        task = store.get(request_id)
        if task is None or str(task.get("status") or "") not in {"awaiting_action", "review", "draft_failed"}:
            return

        actor_id = str((body.get("user") or {}).get("id") or "")
        authority_id = _authority(task)
        if not actor_id or actor_id != authority_id:
            await _not_authorised(client, body, authority_id)
            return
        if not _base_is_current(task):
            store.mark_stale(request_id, decided_by=actor_id)
            stale = store.get(request_id)
            if stale is not None:
                await render_revision_task(client, stale)
            return

        try:
            modal = build_manual_edit_modal(task, _manual_source(task))
        except ValueError:
            channel_id = str((body.get("channel") or {}).get("id") or "")
            if channel_id:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=actor_id,
                    text="This article is too large for safe manual editing in one Slack modal. Use AI drafting for now; a larger-editor workflow can be added separately.",
                )
            return
        await client.views_open(trigger_id=body["trigger_id"], view=modal)

    @app.view(MANUAL_EDIT_MODAL)
    async def save_manual_edit(ack, body, client, view):
        metadata = json.loads(view.get("private_metadata") or "{}")
        request_id = int(metadata.get("request_id") or 0)
        chunk_count = int(metadata.get("chunks") or 0)
        values = (view.get("state") or {}).get("values") or {}
        parts: list[str] = []
        for index in range(chunk_count):
            block = values.get(f"{_MANUAL_BLOCK_PREFIX}{index}") or {}
            action = block.get(f"{_MANUAL_ACTION_PREFIX}{index}") or {}
            parts.append(str(action.get("value") or ""))
        proposed = "".join(parts).strip()
        if not proposed:
            await ack(
                response_action="errors",
                errors={f"{_MANUAL_BLOCK_PREFIX}0": "The article draft cannot be empty."},
            )
            return

        store = get_edit_store()
        task = store.get(request_id) if request_id else None
        actor_id = str((body.get("user") or {}).get("id") or "")
        if task is None or not actor_id or actor_id != _authority(task):
            await ack()
            return
        if not _base_is_current(task):
            await ack()
            store.mark_stale(request_id, decided_by=actor_id)
            stale = store.get(request_id)
            if stale is not None:
                await render_revision_task(client, stale)
            return

        await ack()
        if store.set_manual_draft(request_id, proposed):
            updated = store.get(request_id)
            if updated is not None:
                await render_revision_task(client, updated)

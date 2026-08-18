"""Fast deterministic entry flow for correcting an existing governed article.

Frontend Support detects the intent without an LLM, resolves only articles that
were actually surfaced in the thread, creates the durable task/card immediately,
and schedules Atlas drafting after Slack has acknowledged the action.
"""

from __future__ import annotations

import json
import re

from slack_bolt.async_app import AsyncApp

from src.bot.frontend_edit_actions import build_knowledge_edit_card
from src.bot.knowledge_edit_decisions import register as register_knowledge_edit_decisions
from src.bot.knowledge_edit_drafting import schedule_revision_draft
from src.bot.knowledge_governance_interactivity import get_store as get_governance_store
from src.core.config import settings
from src.knowledge.catalog import KnowledgeCatalog
from src.knowledge.citation_memory import CitationMemory
from src.knowledge.edit_requests import EditRequestStore

FLAG_ARTICLE_EDIT = "frontend_flag_existing_article_edit"
DISMISS_ARTICLE_EDIT = "frontend_dismiss_existing_article_edit"
_EDIT_BLOCK = "frontend_existing_article_edit"

_EDIT_PATTERNS = (
    re.compile(r"\b(?:that|this|the)\s+(?:kb|knowledge)\s*(?:base\s+)?(?:article|page|document)?\s+(?:is\s+)?(?:wrong|outdated|incorrect|stale)\b", re.I),
    re.compile(r"\b(?:update|edit|fix|revise|correct)\s+(?:that|this|the)?\s*(?:kb|knowledge)\s*(?:base\s+)?(?:article|page|document)\b", re.I),
    re.compile(r"\barticle\s+needs?\s+(?:an?\s+)?updat\w*\b", re.I),
    re.compile(r"\bflag\s+(?:that|this|the)\s+(?:article|knowledge)\b.*\breview\b", re.I),
)
_DETAIL_HINTS = (
    " because ",
    " should ",
    " add ",
    " remove ",
    " replace ",
    " change ",
    " after ",
    " before ",
    " instead ",
    " step ",
    " restart ",
    " reinstall ",
    " use ",
)

_citations: CitationMemory | None = None
_edits: EditRequestStore | None = None
_catalog: KnowledgeCatalog | None = None


def get_citations() -> CitationMemory:
    global _citations
    if _citations is None:
        _citations = CitationMemory()
    return _citations


def get_edits() -> EditRequestStore:
    global _edits
    if _edits is None:
        _edits = EditRequestStore()
    return _edits


def get_catalog() -> KnowledgeCatalog:
    global _catalog
    if _catalog is None:
        _catalog = KnowledgeCatalog()
    return _catalog


def looks_like_knowledge_edit(text: str) -> bool:
    clean = " ".join((text or "").split())
    return bool(clean and any(pattern.search(clean) for pattern in _EDIT_PATTERNS))


def has_actionable_edit_detail(text: str) -> bool:
    clean = f" {' '.join((text or '').lower().split())} "
    return len(clean.split()) >= 12 or any(hint in clean for hint in _DETAIL_HINTS)


def _choice_blocks(*, citations: list[dict], edit_note: str, channel_id: str, thread_ts: str) -> list[dict]:
    elements: list[dict] = []
    for citation in citations[:3]:
        value = json.dumps(
            {
                "document_id": citation["document_id"],
                "title": citation["title"],
                "edit_note": edit_note[:1500],
                "source_channel_id": channel_id,
                "source_thread_ts": thread_ts,
            }
        )
        elements.append(
            {
                "type": "button",
                "action_id": FLAG_ARTICLE_EDIT,
                "text": {"type": "plain_text", "text": str(citation["title"])[:75]},
                "value": value,
            }
        )
    elements.append(
        {
            "type": "button",
            "action_id": DISMISS_ARTICLE_EDIT,
            "text": {"type": "plain_text", "text": "Not now"},
            "value": json.dumps({"source_channel_id": channel_id}),
        }
    )
    return [
        {
            "type": "section",
            "block_id": f"{_EDIT_BLOCK}:prompt",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Flag this formal article for correction?*"
                    if len(citations) == 1
                    else "*Which formal article should I flag for correction?*"
                ),
            },
        },
        {"type": "actions", "block_id": f"{_EDIT_BLOCK}:choices", "elements": elements},
    ]


async def offer_knowledge_edit(
    client,
    *,
    channel_id: str,
    thread_ts: str,
    edit_note: str,
) -> bool:
    """Offer a no-LLM edit action when a technician refers to surfaced knowledge."""
    if not has_actionable_edit_detail(edit_note):
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=(
                "I can flag the article, but I need the specific correction first. "
                "Tell me what is wrong or what step should change, for example: "
                "`Update the article: after reinstalling the driver, restart Windows Audio.`"
            ),
        )
        return True

    citations = get_citations().recent(channel_id, thread_ts)
    if not citations:
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=(
                "I can flag a formal article for correction, but no governed article has been surfaced "
                "in this thread yet. Name the article or first ask the question that brings it into the thread."
            ),
        )
        return True

    await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text="Choose the formal article to flag for correction.",
        blocks=_choice_blocks(
            citations=citations,
            edit_note=edit_note,
            channel_id=channel_id,
            thread_ts=thread_ts,
        ),
    )
    return True


def register(app: AsyncApp) -> None:
    register_knowledge_edit_decisions(app)

    @app.action(FLAG_ARTICLE_EDIT)
    async def flag_article_edit(ack, body, client):
        await ack()
        payload = json.loads(body["actions"][0]["value"])
        document_id = str(payload.get("document_id") or "")
        source_channel_id = str(payload.get("source_channel_id") or "")
        source_thread_ts = str(payload.get("source_thread_ts") or "")
        edit_note = str(payload.get("edit_note") or "").strip()
        actor_id = str((body.get("user") or {}).get("id") or "")
        if not all((document_id, source_channel_id, source_thread_ts, edit_note, actor_id)):
            return

        doc = get_catalog().get_document(document_id)
        version = get_catalog().active_version(document_id)
        version_id = str((version or {}).get("version_id") or "")
        if not doc or not version_id:
            await client.chat_postEphemeral(
                channel=source_channel_id,
                user=actor_id,
                text="I couldn't resolve an active governed version for that article, so nothing was changed.",
            )
            return

        task = get_edits().create_drafting(
            channel_id=source_channel_id,
            thread_ts=source_thread_ts,
            document_id=document_id,
            document_title=str(doc.get("title") or payload.get("title") or document_id),
            base_version_id=version_id,
            edit_note=edit_note,
            requested_by=actor_id,
        )

        target_channel = settings.channel_create_knowledge or settings.channel_knowledge_uploads
        if not target_channel:
            get_edits().mark_failed(task["id"], error_message="Create Knowledge channel is not configured")
            await client.chat_postEphemeral(
                channel=source_channel_id,
                user=actor_id,
                text="The correction was captured, but the Create Knowledge channel is not configured.",
            )
            return

        governance = get_governance_store()
        owner = governance.get_owner(document_id)
        pending = governance.pending_reviews_for_article(document_id, version_id=version_id)
        posted = await client.chat_postMessage(
            channel=target_channel,
            text=f"Knowledge edit {task['request_id']} for {task['document_title']}",
            blocks=build_knowledge_edit_card(
                task,
                owner_user_id=str((owner or {}).get("owner_user_id") or "") or None,
                pending_reviews=pending,
            ),
        )
        shared_ts = str((posted or {}).get("ts") or "")
        if not shared_ts:
            get_edits().mark_failed(task["id"], error_message="Slack did not return the shared task message")
            return

        get_edits().attach_shared_message(
            task["id"],
            channel_id=target_channel,
            message_ts=shared_ts,
        )

        source_message = body.get("message") or {}
        source_message_ts = str(source_message.get("ts") or "")
        if source_message_ts:
            await client.chat_update(
                channel=source_channel_id,
                ts=source_message_ts,
                text=(
                    f"✅ Flagged as {task['request_id']} — the review card is in <#{target_channel}> "
                    "and the focused revision is drafting now."
                ),
                blocks=[],
            )

        schedule_revision_draft(client, int(task["id"]))

    @app.action(DISMISS_ARTICLE_EDIT)
    async def dismiss_article_edit(ack, body, client):
        await ack()
        channel_id = str((body.get("channel") or {}).get("id") or "")
        message_ts = str((body.get("message") or {}).get("ts") or "")
        if channel_id and message_ts:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text="No problem — I won't flag that article.",
                blocks=[],
            )

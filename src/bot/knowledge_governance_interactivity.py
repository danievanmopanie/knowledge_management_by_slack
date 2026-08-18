"""Fast Slack ownership/review controls for governed knowledge articles.

All routing in this module is deterministic. Selecting an owner or reviewer
persists immediately, sends a targeted DM, and refreshes the shared governance
section. No LLM is involved.
"""

from __future__ import annotations

import json
import logging

from slack_bolt.async_app import AsyncApp

from src.knowledge.article_governance import ArticleGovernanceStore
from src.knowledge.catalog import KnowledgeCatalog

logger = logging.getLogger(__name__)

ASSIGN_OWNER = "knowledge_governance_assign_owner"
REQUEST_REVIEW = "knowledge_governance_request_review"
REVIEW_MODAL = "knowledge_governance_review_modal"
REVIEWER_BLOCK = "knowledge_governance_reviewer"
REVIEWER_ACTION = "knowledge_governance_reviewer_select"
REVIEW_NOTE_BLOCK = "knowledge_governance_review_note"
REVIEW_NOTE_ACTION = "knowledge_governance_review_note_input"
_BLOCK_PREFIX = "knowledge_governance:"

_store: ArticleGovernanceStore | None = None
_catalog: KnowledgeCatalog | None = None


def get_store() -> ArticleGovernanceStore:
    global _store
    if _store is None:
        _store = ArticleGovernanceStore()
    return _store


def get_catalog() -> KnowledgeCatalog:
    global _catalog
    if _catalog is None:
        _catalog = KnowledgeCatalog()
    return _catalog


def _title(document_id: str) -> str:
    doc = get_catalog().get_document(document_id)
    return str((doc or {}).get("title") or document_id)


def _active_version_id(document_id: str) -> str:
    version = get_catalog().active_version(document_id)
    return str((version or {}).get("version_id") or "")


def build_governance_blocks(
    *,
    document_id: str,
    owner_user_id: str | None = None,
    pending_reviews: list[dict] | None = None,
) -> list[dict]:
    """Compact shared-channel governance section for an article card."""
    owner_text = f"<@{owner_user_id}>" if owner_user_id else "_Unassigned_"
    pending = pending_reviews or []
    if pending:
        reviewers = ", ".join(f"<@{item['reviewer_user_id']}>" for item in pending)
        review_text = reviewers
    else:
        review_text = "_None_"

    return [
        {"type": "divider", "block_id": f"{_BLOCK_PREFIX}divider"},
        {
            "type": "section",
            "block_id": f"{_BLOCK_PREFIX}status",
            "text": {
                "type": "mrkdwn",
                "text": f"*Owner:* {owner_text}\n*Technical review pending:* {review_text}",
            },
        },
        {
            "type": "actions",
            "block_id": f"{_BLOCK_PREFIX}owner:{document_id}",
            "elements": [
                {
                    "type": "users_select",
                    "action_id": ASSIGN_OWNER,
                    "placeholder": {"type": "plain_text", "text": "Assign article owner"},
                    **({"initial_user": owner_user_id} if owner_user_id else {}),
                }
            ],
        },
    ]


def build_owner_dm_blocks(
    *,
    document_id: str,
    title: str,
    assigned_by_user_id: str,
    shared_channel_id: str,
    shared_message_ts: str,
) -> list[dict]:
    value = json.dumps(
        {
            "document_id": document_id,
            "shared_channel_id": shared_channel_id,
            "shared_message_ts": shared_message_ts,
        }
    )
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"You are now the owner of *{title}*.\n"
                    f"Assigned by <@{assigned_by_user_id}>. You are accountable for keeping this article technically current."
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": REQUEST_REVIEW,
                    "text": {"type": "plain_text", "text": "Request technical review"},
                    "style": "primary",
                    "value": value,
                }
            ],
        },
    ]


def build_review_dm_blocks(*, title: str, review: dict) -> list[dict]:
    note = str(review.get("review_note") or "").strip()
    details = f"\n*Requested input:* {note}" if note else ""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"<@{review['requested_by_user_id']}> asked you to provide technical review input for *{title}*."
                    f"{details}\n\nThis review is attached to article version `{review['version_id']}`."
                ),
            },
        }
    ]


def build_review_modal(*, payload: dict, title: str) -> dict:
    return {
        "type": "modal",
        "callback_id": REVIEW_MODAL,
        "private_metadata": json.dumps(payload),
        "title": {"type": "plain_text", "text": "Technical review"},
        "submit": {"type": "plain_text", "text": "Request review"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"Request technical input for *{title}*."},
            },
            {
                "type": "input",
                "block_id": REVIEWER_BLOCK,
                "label": {"type": "plain_text", "text": "Reviewer"},
                "element": {
                    "type": "users_select",
                    "action_id": REVIEWER_ACTION,
                    "placeholder": {"type": "plain_text", "text": "Choose a reviewer"},
                },
            },
            {
                "type": "input",
                "block_id": REVIEW_NOTE_BLOCK,
                "optional": True,
                "label": {"type": "plain_text", "text": "What input do you need?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": REVIEW_NOTE_ACTION,
                    "multiline": True,
                    "max_length": 1200,
                },
            },
        ],
    }


async def _send_dm(client, user_id: str, *, text: str, blocks: list[dict]) -> None:
    opened = await client.conversations_open(users=[user_id])
    channel_id = str(((opened or {}).get("channel") or {}).get("id") or "")
    if not channel_id:
        raise RuntimeError(f"Slack did not return a DM channel for {user_id}")
    await client.chat_postMessage(channel=channel_id, text=text, blocks=blocks)


async def _is_channel_member(client, channel_id: str, user_id: str) -> bool:
    """Validate native users_select choices against the shared article channel."""
    cursor = None
    while True:
        kwargs = {"channel": channel_id, "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        response = await client.conversations_members(**kwargs)
        if user_id in set(response.get("members") or []):
            return True
        cursor = str((((response or {}).get("response_metadata") or {}).get("next_cursor") or "")).strip()
        if not cursor:
            return False


def _strip_governance_blocks(blocks: list[dict]) -> list[dict]:
    return [
        block
        for block in blocks
        if not str(block.get("block_id") or "").startswith(_BLOCK_PREFIX)
    ]


async def _refresh_shared_card(client, *, channel_id: str, message_ts: str, document_id: str) -> None:
    history = await client.conversations_history(
        channel=channel_id,
        latest=message_ts,
        inclusive=True,
        limit=1,
    )
    messages = list((history or {}).get("messages") or [])
    message = next((item for item in messages if str(item.get("ts")) == str(message_ts)), None)
    if not message:
        logger.warning("Could not refresh governance card %s/%s", channel_id, message_ts)
        return

    owner = get_store().get_owner(document_id)
    version_id = _active_version_id(document_id)
    pending = get_store().pending_reviews_for_article(document_id, version_id=version_id or None)
    blocks = _strip_governance_blocks(list(message.get("blocks") or []))
    blocks.extend(
        build_governance_blocks(
            document_id=document_id,
            owner_user_id=str((owner or {}).get("owner_user_id") or "") or None,
            pending_reviews=pending,
        )
    )
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=str(message.get("text") or _title(document_id)),
        blocks=blocks,
    )


def register(app: AsyncApp) -> None:
    @app.action(ASSIGN_OWNER)
    async def assign_owner(ack, body, client):
        await ack()
        action = body["actions"][0]
        selected_user = str(action.get("selected_user") or "")
        block_id = str(action.get("block_id") or "")
        document_id = block_id.split(f"{_BLOCK_PREFIX}owner:", 1)[-1]
        channel_id = str((body.get("channel") or {}).get("id") or "")
        message_ts = str((body.get("message") or {}).get("ts") or "")
        actor_id = str((body.get("user") or {}).get("id") or "")
        if not all((selected_user, document_id, channel_id, message_ts, actor_id)):
            return

        if not await _is_channel_member(client, channel_id, selected_user):
            await client.chat_postEphemeral(
                channel=channel_id,
                user=actor_id,
                text="That person is not a member of this knowledge channel, so ownership was not changed.",
            )
            return

        get_store().assign_owner(
            document_id=document_id,
            owner_user_id=selected_user,
            assigned_by_user_id=actor_id,
        )
        await _refresh_shared_card(
            client,
            channel_id=channel_id,
            message_ts=message_ts,
            document_id=document_id,
        )
        title = _title(document_id)
        await _send_dm(
            client,
            selected_user,
            text=f"You are now the owner of {title}.",
            blocks=build_owner_dm_blocks(
                document_id=document_id,
                title=title,
                assigned_by_user_id=actor_id,
                shared_channel_id=channel_id,
                shared_message_ts=message_ts,
            ),
        )

    @app.action(REQUEST_REVIEW)
    async def request_review(ack, body, client):
        await ack()
        payload = json.loads(body["actions"][0]["value"])
        document_id = str(payload.get("document_id") or "")
        if not document_id:
            return
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=build_review_modal(payload=payload, title=_title(document_id)),
        )

    @app.view(REVIEW_MODAL)
    async def submit_review(ack, body, client, view):
        await ack()
        payload = json.loads(view.get("private_metadata") or "{}")
        values = (view.get("state") or {}).get("values") or {}
        reviewer_id = str(
            (((values.get(REVIEWER_BLOCK) or {}).get(REVIEWER_ACTION) or {}).get("selected_user") or "")
        )
        review_note = str(
            (((values.get(REVIEW_NOTE_BLOCK) or {}).get(REVIEW_NOTE_ACTION) or {}).get("value") or "")
        ).strip()
        document_id = str(payload.get("document_id") or "")
        channel_id = str(payload.get("shared_channel_id") or "")
        message_ts = str(payload.get("shared_message_ts") or "")
        actor_id = str((body.get("user") or {}).get("id") or "")
        if not all((reviewer_id, document_id, channel_id, message_ts, actor_id)):
            return

        if not await _is_channel_member(client, channel_id, reviewer_id):
            await _send_dm(
                client,
                actor_id,
                text="Technical review was not assigned because the selected person is not in the knowledge channel.",
                blocks=[],
            )
            return

        version_id = _active_version_id(document_id)
        if not version_id:
            await _send_dm(
                client,
                actor_id,
                text="Technical review was not assigned because this article has no active governed version.",
                blocks=[],
            )
            return

        try:
            review = get_store().request_review(
                document_id=document_id,
                version_id=version_id,
                reviewer_user_id=reviewer_id,
                requested_by_user_id=actor_id,
                review_note=review_note,
            )
        except Exception:
            logger.exception("Could not create article technical review")
            await _send_dm(
                client,
                actor_id,
                text="That reviewer already has a pending review for this article version.",
                blocks=[],
            )
            return

        title = _title(document_id)
        await _send_dm(
            client,
            reviewer_id,
            text=f"Technical review requested for {title}.",
            blocks=build_review_dm_blocks(title=title, review=review),
        )
        await _refresh_shared_card(
            client,
            channel_id=channel_id,
            message_ts=message_ts,
            document_id=document_id,
        )

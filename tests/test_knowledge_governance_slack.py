import json

import pytest

from src.bot.knowledge_governance_interactivity import (
    ASSIGN_OWNER,
    REQUEST_REVIEW,
    REVIEWER_ACTION,
    REVIEWER_BLOCK,
    _is_channel_member,
    build_governance_blocks,
    build_owner_dm_blocks,
    build_review_modal,
)
from src.knowledge.article_governance import ArticleGovernanceStore


def test_shared_governance_card_uses_native_owner_picker_and_pending_review_state():
    blocks = build_governance_blocks(
        document_id="doc_1",
        owner_user_id="U_OWNER",
        pending_reviews=[{"reviewer_user_id": "U_REVIEWER"}],
    )

    status = blocks[1]["text"]["text"]
    picker = blocks[2]["elements"][0]
    assert "<@U_OWNER>" in status
    assert "<@U_REVIEWER>" in status
    assert picker["type"] == "users_select"
    assert picker["action_id"] == ASSIGN_OWNER
    assert picker["initial_user"] == "U_OWNER"


def test_owner_dm_routes_review_request_back_to_shared_article_card():
    blocks = build_owner_dm_blocks(
        document_id="doc_1",
        title="Laptop Audio Troubleshooting",
        assigned_by_user_id="U_ADMIN",
        shared_channel_id="C_KNOWLEDGE",
        shared_message_ts="123.456",
    )

    button = blocks[1]["elements"][0]
    payload = json.loads(button["value"])
    assert button["action_id"] == REQUEST_REVIEW
    assert payload == {
        "document_id": "doc_1",
        "shared_channel_id": "C_KNOWLEDGE",
        "shared_message_ts": "123.456",
    }


def test_review_modal_uses_slack_user_picker_and_optional_technical_note():
    modal = build_review_modal(
        payload={"document_id": "doc_1"},
        title="Laptop Audio Troubleshooting",
    )

    reviewer = modal["blocks"][1]["element"]
    note = modal["blocks"][2]
    assert reviewer["type"] == "users_select"
    assert reviewer["action_id"] == REVIEWER_ACTION
    assert modal["blocks"][1]["block_id"] == REVIEWER_BLOCK
    assert note["optional"] is True


class _MemberClient:
    def __init__(self):
        self.calls = 0

    async def conversations_members(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "members": ["U_OTHER"],
                "response_metadata": {"next_cursor": "next"},
            }
        return {
            "members": ["U_REVIEWER"],
            "response_metadata": {"next_cursor": ""},
        }


@pytest.mark.anyio
async def test_channel_member_validation_follows_pagination():
    client = _MemberClient()
    assert await _is_channel_member(client, "C_KNOWLEDGE", "U_REVIEWER") is True
    assert client.calls == 2


def test_completed_reviewer_can_be_requested_again_for_same_article_version(tmp_path):
    store = ArticleGovernanceStore(tmp_path / "platform.db")
    first = store.request_review(
        document_id="doc_1",
        version_id="v1",
        reviewer_user_id="U_REVIEWER",
        requested_by_user_id="U_OWNER",
    )
    store.complete_review(first["id"])

    second = store.request_review(
        document_id="doc_1",
        version_id="v1",
        reviewer_user_id="U_REVIEWER",
        requested_by_user_id="U_OWNER",
        review_note="Please reconfirm after the update",
    )

    assert second["status"] == "requested"
    pending = store.pending_reviews_for_article("doc_1", version_id="v1")
    assert [item["id"] for item in pending] == [second["id"]]

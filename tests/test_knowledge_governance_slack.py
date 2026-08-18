import json

import pytest

from src.bot.knowledge_governance_interactivity import (
    ASSIGN_OWNER,
    PROVIDE_REVIEW,
    REQUEST_REVIEW,
    REVIEWER_ACTION,
    REVIEWER_BLOCK,
    REVIEW_RESPONSE_ACTION,
    REVIEW_RESPONSE_BLOCK,
    _is_channel_member,
    build_governance_blocks,
    build_owner_dm_blocks,
    build_review_dm_blocks,
    build_review_modal,
    build_review_response_modal,
)
from src.knowledge.article_governance import ArticleGovernanceStore


def test_shared_governance_card_uses_native_owner_picker_and_review_state():
    blocks = build_governance_blocks(
        document_id="doc_1",
        owner_user_id="U_OWNER",
        pending_reviews=[{"reviewer_user_id": "U_REVIEWER"}],
        completed_reviews=[{"reviewer_user_id": "U_DONE"}],
    )

    status = blocks[1]["text"]["text"]
    picker = blocks[2]["elements"][0]
    assert "<@U_OWNER>" in status
    assert "<@U_REVIEWER>" in status
    assert "<@U_DONE>" in status
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


def test_reviewer_dm_has_direct_input_action_and_response_modal():
    review = {
        "id": 7,
        "document_id": "doc_1",
        "version_id": "v4",
        "reviewer_user_id": "U_REVIEWER",
        "requested_by_user_id": "U_OWNER",
        "review_note": "Validate the restart sequence",
    }
    blocks = build_review_dm_blocks(title="Laptop Audio Troubleshooting", review=review)
    button = blocks[1]["elements"][0]
    assert button["action_id"] == PROVIDE_REVIEW
    assert json.loads(button["value"])["review_id"] == 7

    modal = build_review_response_modal(
        review=review,
        title="Laptop Audio Troubleshooting",
    )
    response_block = modal["blocks"][1]
    response = response_block["element"]
    assert response_block["block_id"] == REVIEW_RESPONSE_BLOCK
    assert response["action_id"] == REVIEW_RESPONSE_ACTION
    assert response["multiline"] is True
    assert response["max_length"] < 3000
    assert response["max_length"] == 2500
    assert response_block["label"]["text"] == "Technical review input"
    assert modal["title"]["text"] == "Technical review"
    assert modal["submit"]["text"] == "Submit review"
    assert "does not publish the article" in modal["blocks"][0]["text"]["text"]
    assert "Validate the restart sequence" in modal["blocks"][0]["text"]["text"]


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
    store.complete_review(
        first["id"],
        reviewer_user_id="U_REVIEWER",
        response_note="Validated.",
    )

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

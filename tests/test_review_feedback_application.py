from types import SimpleNamespace

import pytest

from src.bot.frontend_edit_actions import APPLY_REVIEW_FEEDBACK, build_knowledge_edit_card
from src.knowledge.article_revision import apply_review_feedback
from src.knowledge.edit_requests import EditRequestStore


def _ready_task(tmp_path):
    store = EditRequestStore(tmp_path / "platform.db")
    task = store.create_drafting(
        channel_id="C_SUPPORT",
        thread_ts="123.456",
        document_id="doc_1",
        document_title="Laptop Audio Troubleshooting",
        base_version_id="v4",
        edit_note="Restart Windows Audio after reinstalling the driver.",
        requested_by="U_TECH",
    )
    store.set_draft(task["id"], "Original proposed revision")
    return store, store.get(task["id"])


def _action_ids(blocks):
    return {
        element.get("action_id")
        for block in blocks
        for element in block.get("elements", [])
        if element.get("action_id")
    }


def test_apply_feedback_button_only_shows_for_unapplied_completed_reviews(tmp_path):
    store, task = _ready_task(tmp_path)
    assert task is not None
    completed = [
        {
            "id": 7,
            "reviewer_user_id": "U_REVIEWER",
            "response_note": "Add the Windows Audio restart step.",
        }
    ]

    blocks = build_knowledge_edit_card(task, completed_reviews=completed)
    assert APPLY_REVIEW_FEEDBACK in _action_ids(blocks)

    assert store.start_feedback_draft(task["id"], review_ids=[7]) is True
    store.set_feedback_draft(task["id"], "Updated proposed revision")
    updated = store.get(task["id"])
    assert updated is not None
    assert updated["applied_review_ids"] == [7]

    blocks = build_knowledge_edit_card(updated, completed_reviews=completed)
    assert APPLY_REVIEW_FEEDBACK not in _action_ids(blocks)


def test_feedback_drafting_is_explicit_and_preserves_existing_draft(tmp_path):
    store, task = _ready_task(tmp_path)
    assert task is not None

    assert store.start_feedback_draft(task["id"], review_ids=[3, 2, 3]) is True
    drafting = store.get(task["id"])
    assert drafting is not None
    assert drafting["status"] == "feedback_drafting"
    assert drafting["feedback_review_ids"] == [2, 3]
    assert drafting["proposed_text"] == "Original proposed revision"

    blocks = build_knowledge_edit_card(drafting)
    text = "\n".join(
        str(((block.get("text") or {}).get("text") or "")) for block in blocks
    )
    assert "Applying completed technical review feedback" in text


class _FakeLLM:
    def __init__(self):
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content="Updated article with reviewer feedback")


@pytest.mark.anyio
async def test_review_feedback_llm_receives_existing_draft_and_only_completed_inputs():
    llm = _FakeLLM()
    result = await apply_review_feedback(
        proposed_text="Existing proposed article",
        reviewer_feedback=[
            {
                "reviewer_user_id": "U_REVIEWER",
                "review_note": "Validate service restart",
                "response_note": "Restart Windows Audio after driver installation.",
            }
        ],
        llm=llm,
    )

    assert result == "Updated article with reviewer feedback"
    assert llm.messages is not None
    prompt = str(llm.messages[-1].content)
    assert "Existing proposed article" in prompt
    assert "Restart Windows Audio after driver installation" in prompt
    assert "U_REVIEWER" in prompt

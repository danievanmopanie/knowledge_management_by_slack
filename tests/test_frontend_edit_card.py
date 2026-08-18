from src.bot.frontend_edit_actions import (
    APPROVE_KNOWLEDGE_EDIT,
    DISMISS_KNOWLEDGE_EDIT,
    build_knowledge_edit_card,
)
from src.knowledge.edit_requests import EditRequestStore


def _task(tmp_path):
    store = EditRequestStore(tmp_path / "platform.db")
    return store, store.create_drafting(
        channel_id="C_SUPPORT",
        thread_ts="123.456",
        document_id="doc_1",
        document_title="Laptop Audio Troubleshooting",
        base_version_id="v4",
        edit_note="Restart Windows Audio after reinstalling the driver.",
        requested_by="U_TECH",
    )


def test_edit_task_is_immediately_persisted_in_drafting_state(tmp_path):
    store, task = _task(tmp_path)

    assert task["status"] == "drafting"
    assert task["request_id"] == "KE-00001"
    assert task["base_version_id"] == "v4"
    assert task["proposed_text"] == ""

    store.attach_shared_message(task["id"], channel_id="C_KNOWLEDGE", message_ts="456.789")
    persisted = store.get(task["id"])
    assert persisted is not None
    assert persisted["shared_channel_id"] == "C_KNOWLEDGE"
    assert persisted["shared_message_ts"] == "456.789"


def test_drafting_card_has_governance_controls_before_llm_finishes(tmp_path):
    _store, task = _task(tmp_path)
    blocks = build_knowledge_edit_card(
        task,
        owner_user_id="U_OWNER",
        pending_reviews=[{"reviewer_user_id": "U_REVIEWER"}],
    )

    text = "\n".join(
        str(((block.get("text") or {}).get("text") or "")) for block in blocks
    )
    assert "Drafting focused revision" in text
    assert "<@U_OWNER>" in text
    assert "<@U_REVIEWER>" in text

    owner_picker = next(
        element
        for block in blocks
        for element in block.get("elements", [])
        if element.get("type") == "users_select"
    )
    assert owner_picker["placeholder"]["text"] == "Assign article owner"


def test_same_card_switches_to_revision_preview_and_decision_controls_when_ready(tmp_path):
    store, task = _task(tmp_path)
    store.set_draft(task["id"], "# Laptop Audio\n\nRestart Windows Audio after driver reinstall.")
    ready = store.get(task["id"])
    assert ready is not None

    blocks = build_knowledge_edit_card(ready)
    text = "\n".join(
        str(((block.get("text") or {}).get("text") or "")) for block in blocks
    )
    action_ids = {
        element.get("action_id")
        for block in blocks
        for element in block.get("elements", [])
        if element.get("action_id")
    }

    assert ready["status"] == "review"
    assert "Proposed revision preview" in text
    assert "Restart Windows Audio" in text
    assert "Drafting focused revision" not in text
    assert APPROVE_KNOWLEDGE_EDIT in action_ids
    assert DISMISS_KNOWLEDGE_EDIT in action_ids


def test_stale_and_published_cards_remove_decision_controls(tmp_path):
    store, task = _task(tmp_path)
    store.set_draft(task["id"], "revised")
    store.mark_stale(task["id"], decided_by="U_OWNER")
    stale = store.get(task["id"])
    assert stale is not None
    stale_blocks = build_knowledge_edit_card(stale)
    stale_text = "\n".join(
        str(((block.get("text") or {}).get("text") or "")) for block in stale_blocks
    )
    stale_actions = {
        element.get("action_id")
        for block in stale_blocks
        for element in block.get("elements", [])
        if element.get("action_id")
    }
    assert "Stale draft" in stale_text
    assert APPROVE_KNOWLEDGE_EDIT not in stale_actions

    store.dismiss(task["id"], decided_by="U_OWNER")
    dismissed = store.get(task["id"])
    assert dismissed is not None
    dismissed_blocks = build_knowledge_edit_card(dismissed)
    dismissed_text = "\n".join(
        str(((block.get("text") or {}).get("text") or "")) for block in dismissed_blocks
    )
    assert "Dismissed by <@U_OWNER>" in dismissed_text

from src.bot.frontend_edit_actions import build_knowledge_edit_card
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


def test_same_card_switches_to_revision_preview_when_draft_is_ready(tmp_path):
    store, task = _task(tmp_path)
    store.set_draft(task["id"], "# Laptop Audio\n\nRestart Windows Audio after driver reinstall.")
    ready = store.get(task["id"])
    assert ready is not None

    blocks = build_knowledge_edit_card(ready)
    text = "\n".join(
        str(((block.get("text") or {}).get("text") or "")) for block in blocks
    )

    assert ready["status"] == "review"
    assert "Proposed revision preview" in text
    assert "Restart Windows Audio" in text
    assert "Drafting focused revision" not in text

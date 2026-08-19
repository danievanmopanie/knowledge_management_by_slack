from src.bot.frontend_edit_actions import (
    APPROVE_KNOWLEDGE_EDIT,
    DISMISS_KNOWLEDGE_EDIT,
    DRAFT_KNOWLEDGE_EDIT_WITH_AI,
    EDIT_KNOWLEDGE_EDIT_MANUALLY,
    VIEW_FULL_KNOWLEDGE_DRAFT,
    build_knowledge_edit_card,
    compact_revision_diff,
)
from src.bot.knowledge_edit_decisions import build_full_draft_modal
from src.bot.knowledge_edit_workflow import build_manual_edit_modal
from src.knowledge.edit_requests import EditRequestStore


def _task(tmp_path):
    store = EditRequestStore(tmp_path / "platform.db")
    return store, store.create_pending(
        channel_id="C_SUPPORT",
        thread_ts="123.456",
        document_id="doc_1",
        document_title="Laptop Audio Troubleshooting",
        base_version_id="v4",
        edit_note="Restart Windows Audio after reinstalling the driver.",
        requested_by="U_TECH",
    )


def _make_ready(store, task, proposed):
    assert store.start_ai_draft(task["id"]) is True
    store.set_draft(task["id"], proposed)
    ready = store.get(task["id"])
    assert ready is not None
    return ready


def test_edit_task_is_immediately_persisted_without_starting_llm(tmp_path):
    store, task = _task(tmp_path)

    assert task["status"] == "awaiting_action"
    assert task["request_id"] == "KE-00001"
    assert task["base_version_id"] == "v4"
    assert task["proposed_text"] == ""

    store.attach_shared_message(task["id"], channel_id="C_KNOWLEDGE", message_ts="456.789")
    persisted = store.get(task["id"])
    assert persisted is not None
    assert persisted["shared_channel_id"] == "C_KNOWLEDGE"
    assert persisted["shared_message_ts"] == "456.789"


def test_initial_card_requires_explicit_ai_or_manual_drafting_choice(tmp_path):
    _store, task = _task(tmp_path)
    blocks = build_knowledge_edit_card(
        task,
        owner_user_id="U_OWNER",
        pending_reviews=[{"reviewer_user_id": "U_REVIEWER"}],
    )

    text = "\n".join(
        str(((block.get("text") or {}).get("text") or "")) for block in blocks
    )
    action_ids = {
        element.get("action_id")
        for block in blocks
        for element in block.get("elements", [])
        if element.get("action_id")
    }
    assert "Action needed — not published" in text
    assert "No AI work has started yet" in text
    assert "<@U_OWNER>" in text
    assert "<@U_REVIEWER>" in text
    assert DRAFT_KNOWLEDGE_EDIT_WITH_AI in action_ids
    assert EDIT_KNOWLEDGE_EDIT_MANUALLY in action_ids
    assert APPROVE_KNOWLEDGE_EDIT not in action_ids

    owner_picker = next(
        element
        for block in blocks
        for element in block.get("elements", [])
        if element.get("type") == "users_select"
    )
    assert owner_picker["placeholder"]["text"] == "Assign article owner"


def test_ready_card_shows_changed_lines_manual_edit_and_confirmed_publish_action(tmp_path):
    store, task = _task(tmp_path)
    current = "# Laptop Audio\n\nReinstall the audio driver.\n\nCheck volume."
    proposed = "# Laptop Audio\n\nReinstall the audio driver.\nRestart Windows Audio.\n\nCheck volume."
    ready = _make_ready(store, task, proposed)

    blocks = build_knowledge_edit_card(ready, current_text=current)
    text = "\n".join(
        str(((block.get("text") or {}).get("text") or "")) for block in blocks
    )
    actions = [
        element
        for block in blocks
        for element in block.get("elements", [])
        if element.get("action_id")
    ]
    action_ids = {element["action_id"] for element in actions}

    assert ready["status"] == "review"
    assert "Draft ready for review — not published" in text
    assert "What changed" in text
    assert "+ Restart Windows Audio." in text
    assert VIEW_FULL_KNOWLEDGE_DRAFT in action_ids
    assert EDIT_KNOWLEDGE_EDIT_MANUALLY in action_ids
    assert APPROVE_KNOWLEDGE_EDIT in action_ids
    assert DISMISS_KNOWLEDGE_EDIT in action_ids
    publish = next(item for item in actions if item["action_id"] == APPROVE_KNOWLEDGE_EDIT)
    assert publish["confirm"]["confirm"]["text"] == "Publish revision"
    assert "new governed knowledge version" in publish["confirm"]["text"]["text"]


def test_manual_edit_modal_is_slack_safe_and_explicitly_non_publishing(tmp_path):
    _store, task = _task(tmp_path)
    text = "A" * 6200
    modal = build_manual_edit_modal(task, text)

    inputs = [block["element"] for block in modal["blocks"] if block.get("type") == "input"]
    assert len(inputs) == 3
    assert all(element["max_length"] < 3000 for element in inputs)
    assert all(len(element["initial_value"]) <= 2400 for element in inputs)
    assert modal["submit"]["text"] == "Save draft"
    intro = modal["blocks"][0]["text"]["text"]
    assert "does *not* publish" in intro


def test_compact_diff_omits_unchanged_lines():
    diff = compact_revision_diff(
        "Heading\nKeep this\nOld step\nAlso keep this",
        "Heading\nKeep this\nNew step\nAlso keep this",
    )
    assert "- Old step" in diff
    assert "+ New step" in diff
    assert "Keep this" not in diff
    assert "Also keep this" not in diff


def test_full_draft_modal_contains_complete_normal_sized_draft(tmp_path):
    store, task = _task(tmp_path)
    proposed = "# Laptop Audio\n\n" + ("Detailed technical procedure.\n" * 120)
    ready = _make_ready(store, task, proposed)

    modal = build_full_draft_modal(ready)
    rendered = "".join(
        str(((block.get("text") or {}).get("text") or ""))
        for block in modal["blocks"]
        if block.get("type") == "section"
    )
    assert "# Laptop Audio" in rendered
    assert rendered.count("Detailed technical procedure.") == 120
    assert modal["title"]["text"] == "Full draft"


def test_stale_and_dismissed_cards_remove_decision_controls(tmp_path):
    store, task = _task(tmp_path)
    ready = _make_ready(store, task, "revised")
    store.mark_stale(ready["id"], decided_by="U_OWNER")
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
    assert "Stale revision" in stale_text
    assert APPROVE_KNOWLEDGE_EDIT not in stale_actions
    assert VIEW_FULL_KNOWLEDGE_DRAFT not in stale_actions

    store.dismiss(task["id"], decided_by="U_OWNER")
    dismissed = store.get(task["id"])
    assert dismissed is not None
    dismissed_blocks = build_knowledge_edit_card(dismissed)
    dismissed_text = "\n".join(
        str(((block.get("text") or {}).get("text") or "")) for block in dismissed_blocks
    )
    assert "Dismissed by <@U_OWNER>" in dismissed_text

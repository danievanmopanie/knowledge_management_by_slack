from pathlib import Path

import pytest

from src.bot.blockkit import ids
from src.bot.knowledge_candidate_blocks import candidate_card, frontend_candidate_actions
from src.knowledge import knowledge_candidates as candidates_module
from src.knowledge.knowledge_candidates import KnowledgeCandidateStore


def _create(store: KnowledgeCandidateStore):
    return store.create(
        incident_number="INC0092846",
        title="Babylon employee profile deletion",
        issue_pattern="Babylon employee profile deletion",
        symptom="Employee profile missing in Babylon",
        recorded_resolution="Employees were replicated and surfaced on site",
        resolution_pattern="Replicate Babylon employee profiles",
        knowledge_gap="Document diagnosis, exact replication procedure and validation.",
        requested_by="U1",
        source_channel_id="C-FRONT",
        source_thread_ts="123.45",
    )[0]


def test_candidate_is_durable_deduplicated_and_not_reviewable_until_enriched(tmp_path: Path):
    store = KnowledgeCandidateStore(tmp_path / "platform.db")
    first = _create(store)
    second, created = store.create(
        incident_number="INC0092846",
        title="Duplicate attempt",
        requested_by="U2",
    )

    assert first.candidate_id == "KC-000001"
    assert second.candidate_id == first.candidate_id
    assert created is False
    assert first.status == "needs_enrichment"
    assert first.missing_review_fields == ("applies_when", "procedure", "validation")

    with pytest.raises(ValueError, match="Missing required knowledge fields"):
        store.mark_ready(first.candidate_id)


def test_candidate_review_gate_and_publish_build_governed_document(tmp_path: Path, monkeypatch):
    store = KnowledgeCandidateStore(tmp_path / "platform.db")
    candidate = _create(store)
    candidate = store.update_content(
        candidate.candidate_id,
        applies_when="Use when an employee exists in the upstream personnel sources but is missing in Babylon.",
        procedure="Verify the employee in the source systems, then run the approved Babylon employee replication process.",
        validation="Confirm the employee profile surfaces in Babylon on the target site and the requester can find it.",
        prerequisites="Use an account authorised to perform employee replication.",
    )
    ready = store.mark_ready(candidate.candidate_id)
    assert ready.status == "ready_for_review"

    captured = {}

    def fake_commit_knowledge(**kwargs):
        captured.update(kwargs)
        return {"document_id": "DOC-1", "version_id": "VER-1", "unchanged": False, "chunks": 2}

    monkeypatch.setattr(candidates_module, "commit_knowledge", fake_commit_knowledge)
    published, result = store.publish(ready.candidate_id, reviewer_id="U-REVIEWER")

    assert published.status == "published"
    assert published.reviewer_id == "U-REVIEWER"
    assert published.published_document_id == "DOC-1"
    assert result["chunks"] == 2
    assert "## When this applies" in captured["text"]
    assert "## Resolution procedure" in captured["text"]
    assert "## Validation" in captured["text"]
    assert "Source incident: INC0092846" in captured["text"]


def test_candidate_cards_are_button_first_and_publication_is_explicit(tmp_path: Path):
    store = KnowledgeCandidateStore(tmp_path / "platform.db")
    candidate = _create(store)
    blocks = candidate_card(candidate)
    action_ids = {
        element.get("action_id")
        for block in blocks
        if block.get("type") == "actions"
        for element in block.get("elements", [])
    }

    assert ids.KNOWLEDGE_CANDIDATE_CLAIM in action_ids
    assert ids.KNOWLEDGE_CANDIDATE_EDIT in action_ids
    assert ids.KNOWLEDGE_CANDIDATE_REQUEST_INPUT in action_ids
    assert ids.KNOWLEDGE_CANDIDATE_READY in action_ids
    assert ids.KNOWLEDGE_CANDIDATE_APPROVE not in action_ids

    frontend = frontend_candidate_actions(
        incident_number="INC0092846",
        channel_id="C-FRONT",
        thread_ts="123.45",
    )
    frontend_ids = {
        element.get("action_id")
        for block in frontend
        if block.get("type") == "actions"
        for element in block.get("elements", [])
    }
    assert ids.FRONTEND_CREATE_KNOWLEDGE_CANDIDATE in frontend_ids
    assert ids.FRONTEND_FLAG_KNOWLEDGE_GAP in frontend_ids

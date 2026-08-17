"""Tests for flagging and revising an existing knowledge article from #frontend-support."""

import asyncio
import json
from unittest.mock import AsyncMock

from src.bot import frontend_edit_interactivity
from src.bot.frontend_edit_actions import (
    APPROVE_KNOWLEDGE_EDIT,
    ASSIGN_KNOWLEDGE_EDIT,
    DISMISS_KNOWLEDGE_EDIT,
    KNOWLEDGE_EDIT_ACTIONS_BLOCK_PREFIX,
    KNOWLEDGE_EDIT_ASSIGN_BLOCK_PREFIX,
    PROPOSE_KNOWLEDGE_EDIT,
    build_knowledge_edit_review_blocks,
)
from src.knowledge.citation_memory import CitationMemory
from src.knowledge.edit_requests import EditRequestStore


class FakeApp:
    def __init__(self):
        self.actions = {}

    def action(self, action_id):
        def register(handler):
            self.actions[action_id] = handler
            return handler

        return register


def registered_app():
    app = FakeApp()
    frontend_edit_interactivity.register(app)
    return app


def fake_client():
    client = AsyncMock()
    client.conversations_open.return_value = {"channel": {"id": "DM123"}}
    return client


async def _fake_revise(*, current_text, edit_note, llm=None):
    return f"# Revised\n\nApplied: {edit_note}"


class FakeCatalog:
    def get_document(self, doc_id):
        return {"source_id": "slack-file-1", "title": "VPN Runbook", "source_system": "slack"}


def test_propose_prompt_without_citation_asks_for_the_article(monkeypatch, tmp_path):
    citations = CitationMemory(tmp_path / "platform.db")
    monkeypatch.setattr(frontend_edit_interactivity, "get_citation_memory", lambda: citations)
    client = fake_client()

    asyncio.run(
        frontend_edit_interactivity.propose_knowledge_edit_prompt(
            client=client, channel_id="C-FRONT", thread_ts="100.1", edit_note="It's wrong."
        )
    )

    _, kwargs = client.chat_postMessage.call_args
    assert "don't have one cited" in kwargs["text"]
    assert "blocks" not in kwargs or kwargs.get("blocks") is None


def test_propose_prompt_with_citation_offers_to_flag_it(monkeypatch, tmp_path):
    citations = CitationMemory(tmp_path / "platform.db")
    citations.record(channel_id="C-FRONT", thread_ts="100.1", document_id="doc_1", title="VPN Runbook")
    monkeypatch.setattr(frontend_edit_interactivity, "get_citation_memory", lambda: citations)
    client = fake_client()

    asyncio.run(
        frontend_edit_interactivity.propose_knowledge_edit_prompt(
            client=client, channel_id="C-FRONT", thread_ts="100.1", edit_note="It's wrong."
        )
    )

    _, kwargs = client.chat_postMessage.call_args
    assert "VPN Runbook" in kwargs["text"]
    assert kwargs["blocks"]


def test_propose_knowledge_edit_action_drafts_revision_and_posts_review_card(monkeypatch, tmp_path):
    citations = CitationMemory(tmp_path / "platform.db")
    citations.record(channel_id="C-FRONT", thread_ts="100.1", document_id="doc_1", title="VPN Runbook")
    edit_requests = EditRequestStore(tmp_path / "platform.db")
    monkeypatch.setattr(frontend_edit_interactivity, "get_citation_memory", lambda: citations)
    monkeypatch.setattr(frontend_edit_interactivity, "get_edit_request_store", lambda: edit_requests)
    monkeypatch.setattr(frontend_edit_interactivity, "revise_article", _fake_revise)
    monkeypatch.setattr(
        frontend_edit_interactivity, "reconstruct_document_text", lambda document_id, **_: "Original text"
    )
    monkeypatch.setattr(frontend_edit_interactivity, "KnowledgeCatalog", FakeCatalog)
    monkeypatch.setattr(frontend_edit_interactivity.settings, "channel_create_knowledge", "C-CREATE")

    app = registered_app()
    client = fake_client()
    body = {
        "actions": [
            {"value": json.dumps({"channel_id": "C-FRONT", "thread_ts": "100.1", "edit_note": "Add step X."})}
        ],
        "user": {"id": "U-REQ"},
        "channel": {"id": "C-FRONT"},
        "message": {"ts": "200.1", "text": "prompt", "blocks": []},
    }

    asyncio.run(app.actions[PROPOSE_KNOWLEDGE_EDIT](AsyncMock(), body, client))

    client.chat_update.assert_called_once()
    client.chat_postMessage.assert_called_once()
    _, kwargs = client.chat_postMessage.call_args
    assert kwargs["channel"] == "C-CREATE"
    blocks_text = json.dumps(kwargs["blocks"])
    assert "Add step X." in blocks_text
    assert "Applied: Add step X." in blocks_text

    task = edit_requests.get(1)
    assert task["document_title"] == "VPN Runbook"
    assert task["status"] == "open"


def test_propose_knowledge_edit_action_without_citation_flags_nothing(monkeypatch, tmp_path):
    citations = CitationMemory(tmp_path / "platform.db")  # nothing recorded
    edit_requests = EditRequestStore(tmp_path / "platform.db")
    monkeypatch.setattr(frontend_edit_interactivity, "get_citation_memory", lambda: citations)
    monkeypatch.setattr(frontend_edit_interactivity, "get_edit_request_store", lambda: edit_requests)

    app = registered_app()
    client = fake_client()
    body = {
        "actions": [
            {"value": json.dumps({"channel_id": "C-FRONT", "thread_ts": "100.1", "edit_note": "Add step X."})}
        ],
        "user": {"id": "U-REQ"},
        "channel": {"id": "C-FRONT"},
        "message": {"ts": "200.1", "text": "prompt", "blocks": []},
    }

    asyncio.run(app.actions[PROPOSE_KNOWLEDGE_EDIT](AsyncMock(), body, client))

    client.chat_postMessage.assert_not_called()
    client.chat_update.assert_called_once()
    assert edit_requests.get(1) is None


def test_approve_knowledge_edit_republishes_under_the_same_document_identity(monkeypatch, tmp_path):
    edit_requests = EditRequestStore(tmp_path / "platform.db")
    task = edit_requests.create(
        channel_id="C-FRONT",
        thread_ts="100.1",
        document_id="doc_1",
        document_title="VPN Runbook",
        edit_note="Add step X.",
        proposed_text="# VPN Runbook\n\nRevised text.",
        requested_by="U-REQ",
    )
    monkeypatch.setattr(frontend_edit_interactivity, "get_edit_request_store", lambda: edit_requests)
    monkeypatch.setattr(frontend_edit_interactivity, "KnowledgeCatalog", FakeCatalog)

    committed = {}

    def fake_commit_knowledge(*, text, title, source_id, owner_id):
        committed.update(text=text, title=title, source_id=source_id)
        return {"document_id": "doc_1", "chunks": 3, "unchanged": False}

    monkeypatch.setattr(frontend_edit_interactivity, "commit_knowledge", fake_commit_knowledge)

    app = registered_app()
    client = fake_client()
    body = {
        "actions": [{"value": json.dumps({"request_id": task["id"]})}],
        "user": {"id": "U-REVIEWER"},
        "channel": {"id": "C-CREATE"},
        "message": {"ts": "300.1", "text": "card", "blocks": build_knowledge_edit_review_blocks(task)},
    }

    asyncio.run(app.actions[APPROVE_KNOWLEDGE_EDIT](AsyncMock(), body, client))

    # Same source_id as the original document => a new version of the same
    # article, not a second, duplicate document.
    assert committed["source_id"] == "slack-file-1"
    assert committed["text"] == "# VPN Runbook\n\nRevised text."
    updated = edit_requests.get(task["id"])
    assert updated["status"] == "published"

    updated_blocks = client.chat_update.call_args.kwargs["blocks"]
    block_ids = [b.get("block_id", "") for b in updated_blocks]
    assert not any(bid.startswith(KNOWLEDGE_EDIT_ACTIONS_BLOCK_PREFIX) for bid in block_ids)


def test_assign_knowledge_edit_sends_dm_and_updates_card(monkeypatch, tmp_path):
    edit_requests = EditRequestStore(tmp_path / "platform.db")
    task = edit_requests.create(
        channel_id="C-FRONT",
        thread_ts="100.1",
        document_id="doc_1",
        document_title="VPN Runbook",
        edit_note="Add step X.",
        proposed_text="# VPN Runbook\n\nRevised.",
        requested_by="U-REQ",
    )
    monkeypatch.setattr(frontend_edit_interactivity, "get_edit_request_store", lambda: edit_requests)
    monkeypatch.setattr(frontend_edit_interactivity.settings, "channel_create_knowledge", "C-CREATE")

    app = registered_app()
    client = fake_client()
    body = {
        "actions": [{"block_id": f"frontend_knowledge_edit_assign:{task['id']}", "selected_user": "U-ASSIGNEE"}],
        "user": {"id": "U-MANAGER"},
        "channel": {"id": "C-CREATE"},
        "message": {"ts": "300.1", "text": "card", "blocks": build_knowledge_edit_review_blocks(task)},
    }

    asyncio.run(app.actions[ASSIGN_KNOWLEDGE_EDIT](AsyncMock(), body, client))

    client.conversations_open.assert_called_once_with(users="U-ASSIGNEE")
    dm_call = client.chat_postMessage.call_args_list[0]
    assert dm_call.kwargs["channel"] == "DM123"
    assert task["request_id"] in dm_call.kwargs["text"]

    updated = edit_requests.get(task["id"])
    assert updated["assignee_id"] == "U-ASSIGNEE"
    assert updated["status"] == "assigned"

    updated_blocks = client.chat_update.call_args.kwargs["blocks"]
    block_ids = [b.get("block_id", "") for b in updated_blocks]
    assert any(bid.startswith(KNOWLEDGE_EDIT_ACTIONS_BLOCK_PREFIX) for bid in block_ids)


def test_dismiss_knowledge_edit_marks_dismissed_and_removes_actions(monkeypatch, tmp_path):
    edit_requests = EditRequestStore(tmp_path / "platform.db")
    task = edit_requests.create(
        channel_id="C-FRONT",
        thread_ts="100.1",
        document_id="doc_1",
        document_title="VPN Runbook",
        edit_note="Add step X.",
        proposed_text="# VPN Runbook\n\nRevised.",
        requested_by="U-REQ",
    )
    monkeypatch.setattr(frontend_edit_interactivity, "get_edit_request_store", lambda: edit_requests)

    app = registered_app()
    client = fake_client()
    body = {
        "actions": [{"value": json.dumps({"request_id": task["id"]})}],
        "user": {"id": "U-REVIEWER"},
        "channel": {"id": "C-CREATE"},
        "message": {"ts": "300.1", "text": "card", "blocks": build_knowledge_edit_review_blocks(task)},
    }

    asyncio.run(app.actions[DISMISS_KNOWLEDGE_EDIT](AsyncMock(), body, client))

    updated = edit_requests.get(task["id"])
    assert updated["status"] == "dismissed"
    updated_blocks = client.chat_update.call_args.kwargs["blocks"]
    block_ids = [b.get("block_id", "") for b in updated_blocks]
    assert not any(bid.startswith(KNOWLEDGE_EDIT_ACTIONS_BLOCK_PREFIX) for bid in block_ids)

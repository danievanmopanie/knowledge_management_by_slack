"""Tests for the simplified, AI-drafted create-knowledge card flow."""

import asyncio
import json
from unittest.mock import AsyncMock

from src.agents.frontend_support.collaboration import FrontendCollaborationService, FrontendThreadStore
from src.bot import frontend_interactivity
from src.bot.frontend_actions import (
    ASSIGN_KNOWLEDGE,
    DISMISS_KNOWLEDGE,
    KNOWLEDGE_ACTIONS_BLOCK_PREFIX,
    KNOWLEDGE_ASSIGN_BLOCK_PREFIX,
    PUBLISH_KNOWLEDGE,
    UPDATE_EXISTING_KNOWLEDGE,
    build_knowledge_task_blocks,
)
from src.knowledge.article_drafting import DraftedArticle
from src.knowledge.graphstore import GraphStore
from src.knowledge.retrieval_models import RetrievalCandidate, RetrievalResult
from src.knowledge.support_graph import SupportKnowledgeGraph


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
    frontend_interactivity.register(app)
    return app


def fake_client():
    client = AsyncMock()
    client.conversations_open.return_value = {"channel": {"id": "DM123"}}
    return client


class FixedRetriever:
    def __init__(self, result: RetrievalResult):
        self.result = result

    def search(self, request):
        return self.result


def _service(tmp_path):
    return FrontendCollaborationService(
        store=FrontendThreadStore(tmp_path / "platform.db"),
        graph=SupportKnowledgeGraph(GraphStore(path=tmp_path / "graph")),
    )


def _confirmed_service(tmp_path, *, channel="C-FRONT", thread="100.1"):
    service = _service(tmp_path)
    service.observe(
        channel_id=channel,
        message_ts=thread,
        thread_ts=None,
        user_id="U-REQ",
        text="Outlook keeps prompting the user for a password and is not working.",
    )
    service.observe(
        channel_id=channel,
        message_ts="100.2",
        thread_ts=thread,
        user_id="U-REQ",
        text="Incident is INC0055555.",
    )
    service.observe(
        channel_id=channel,
        message_ts="100.3",
        thread_ts=thread,
        user_id="U-REQ",
        text="Cleared the WAM token cache and restarted Outlook.",
    )
    service.observe(
        channel_id=channel,
        message_ts="100.4",
        thread_ts=thread,
        user_id="U-REQ",
        text="That's fixed it, working now.",
    )
    result = service.confirm_knowledge(channel, thread, "U-REQ")
    assert result.startswith("Captured this resolution")
    return service


async def _fake_draft(**kwargs):
    return DraftedArticle(
        title="Outlook WAM token cache fix",
        markdown="# Outlook WAM token cache fix\n\n## Symptom\nOutlook prompts for credentials.\n",
    )


def test_strong_duplicate_match_skips_task_creation(tmp_path, monkeypatch):
    service = _confirmed_service(tmp_path)
    monkeypatch.setattr(frontend_interactivity, "get_service", lambda: service)
    result = RetrievalResult(
        candidates=[
            RetrievalCandidate(evidence_id="e1", content="...", metadata={"source": "Outlook WAM KB"})
        ],
        confidence_score=0.9,
        evidence_level="strong",
    )
    monkeypatch.setattr(frontend_interactivity, "get_retriever", lambda: FixedRetriever(result))
    client = fake_client()

    asyncio.run(
        frontend_interactivity._propose_knowledge_task(
            client=client, channel_id="C-FRONT", thread_ts="100.1", actor_id="U-REQ"
        )
    )

    assert client.chat_postMessage.call_count == 1
    _, kwargs = client.chat_postMessage.call_args
    assert "won't create a duplicate article" in kwargs["text"]
    assert "Outlook WAM KB" in kwargs["text"]


def test_weak_duplicate_match_flags_related_article(tmp_path, monkeypatch):
    service = _confirmed_service(tmp_path)
    monkeypatch.setattr(frontend_interactivity, "get_service", lambda: service)
    monkeypatch.setattr(frontend_interactivity.settings, "channel_create_knowledge", "C-CREATE")
    result = RetrievalResult(
        candidates=[
            RetrievalCandidate(
                evidence_id="e1",
                content="...",
                metadata={"source": "Old Outlook KB", "document_id": "doc_old_123"},
            )
        ],
        confidence_score=0.4,
        evidence_level="weak",
    )
    monkeypatch.setattr(frontend_interactivity, "get_retriever", lambda: FixedRetriever(result))
    monkeypatch.setattr(frontend_interactivity, "draft_knowledge_article", _fake_draft)
    client = fake_client()

    asyncio.run(
        frontend_interactivity._propose_knowledge_task(
            client=client, channel_id="C-FRONT", thread_ts="100.1", actor_id="U-REQ"
        )
    )

    assert client.chat_postMessage.call_count == 2
    card_call = client.chat_postMessage.call_args_list[0]
    assert card_call.kwargs["channel"] == "C-CREATE"
    blocks_text = json.dumps(card_call.kwargs["blocks"])
    assert "Old Outlook KB" in blocks_text
    assert "Update existing article instead" in blocks_text

    task = service.store.get_knowledge_task(1)
    assert task["related_document_id"] == "doc_old_123"
    assert task["drafted_title"] == "Outlook WAM token cache fix"


def test_insufficient_evidence_drafts_fresh_article_without_related_flag(tmp_path, monkeypatch):
    service = _confirmed_service(tmp_path)
    monkeypatch.setattr(frontend_interactivity, "get_service", lambda: service)
    monkeypatch.setattr(frontend_interactivity.settings, "channel_create_knowledge", "C-CREATE")
    result = RetrievalResult(candidates=[], confidence_score=0.0, evidence_level="insufficient")
    monkeypatch.setattr(frontend_interactivity, "get_retriever", lambda: FixedRetriever(result))
    monkeypatch.setattr(frontend_interactivity, "draft_knowledge_article", _fake_draft)
    client = fake_client()

    asyncio.run(
        frontend_interactivity._propose_knowledge_task(
            client=client, channel_id="C-FRONT", thread_ts="100.1", actor_id="U-REQ"
        )
    )

    task = service.store.get_knowledge_task(1)
    assert task["related_document_id"] is None

    blocks = build_knowledge_task_blocks(task)
    blocks_text = json.dumps(blocks)
    assert "Update existing article instead" not in blocks_text
    assert "Publish new article" in blocks_text


def test_publish_knowledge_commits_article_and_updates_card(tmp_path, monkeypatch):
    service = _confirmed_service(tmp_path)
    task = service.store.create_knowledge_task(
        channel_id="C-FRONT",
        thread_ts="100.1",
        created_by="U-REQ",
        reason="test",
        drafted_title="Outlook WAM token cache fix",
        drafted_markdown="# Outlook WAM token cache fix\n\n## Resolution steps\n1. Clear cache.\n",
    )
    monkeypatch.setattr(frontend_interactivity, "get_service", lambda: service)

    committed = {}

    def fake_commit_knowledge(*, text, title, source_id, owner_id, **_kwargs):
        committed.update(text=text, title=title, source_id=source_id, owner_id=owner_id)
        return {"document_id": "doc_new_1", "chunks": 3, "unchanged": False}

    monkeypatch.setattr(frontend_interactivity, "commit_knowledge", fake_commit_knowledge)

    app = registered_app()
    client = fake_client()
    body = {
        "actions": [{"value": json.dumps({"task_id": task["id"]})}],
        "user": {"id": "U-REVIEWER"},
        "channel": {"id": "C-CREATE"},
        "message": {"ts": "500.1", "text": "card", "blocks": build_knowledge_task_blocks(task)},
    }

    asyncio.run(app.actions[PUBLISH_KNOWLEDGE](AsyncMock(), body, client))

    assert committed["source_id"] == f"knowledge-task-{task['id']}"
    assert committed["title"] == "Outlook WAM token cache fix"

    updated = service.store.get_knowledge_task(task["id"])
    assert updated["status"] == "published"
    assert updated["published_document_id"] == "doc_new_1"

    client.chat_update.assert_called_once()
    updated_blocks = client.chat_update.call_args.kwargs["blocks"]
    block_ids = [b.get("block_id", "") for b in updated_blocks]
    assert not any(bid.startswith(KNOWLEDGE_ACTIONS_BLOCK_PREFIX) for bid in block_ids)
    assert not any(bid.startswith(KNOWLEDGE_ASSIGN_BLOCK_PREFIX) for bid in block_ids)
    assert "Published by <@U-REVIEWER>" in json.dumps(updated_blocks)


def test_update_existing_knowledge_reuses_original_document_identity(tmp_path, monkeypatch):
    service = _confirmed_service(tmp_path)
    task = service.store.create_knowledge_task(
        channel_id="C-FRONT",
        thread_ts="100.1",
        created_by="U-REQ",
        reason="test",
        related_document_id="doc_old_123",
        related_document_title="Old Outlook KB",
        drafted_title="Outlook WAM token cache fix",
        drafted_markdown="# Outlook WAM token cache fix\n",
    )
    monkeypatch.setattr(frontend_interactivity, "get_service", lambda: service)

    class FakeCatalog:
        def get_document(self, doc_id):
            assert doc_id == "doc_old_123"
            return {"source_id": "slack-file-old", "title": "Old Outlook KB", "source_system": "slack"}

    monkeypatch.setattr(frontend_interactivity, "KnowledgeCatalog", FakeCatalog)

    committed = {}

    def fake_commit_knowledge(*, text, title, source_id, owner_id, **_kwargs):
        committed.update(source_id=source_id, title=title)
        return {"document_id": "doc_old_123", "chunks": 2, "unchanged": False}

    monkeypatch.setattr(frontend_interactivity, "commit_knowledge", fake_commit_knowledge)

    app = registered_app()
    client = fake_client()
    body = {
        "actions": [{"value": json.dumps({"task_id": task["id"]})}],
        "user": {"id": "U-REVIEWER"},
        "channel": {"id": "C-CREATE"},
        "message": {"ts": "500.1", "text": "card", "blocks": build_knowledge_task_blocks(task)},
    }

    asyncio.run(app.actions[UPDATE_EXISTING_KNOWLEDGE](AsyncMock(), body, client))

    # Same source_id as the original document => a new version of the same
    # article, not a second, duplicate document.
    assert committed["source_id"] == "slack-file-old"
    updated = service.store.get_knowledge_task(task["id"])
    assert updated["status"] == "published"


def test_assign_knowledge_sends_dm_and_updates_card(tmp_path, monkeypatch):
    service = _confirmed_service(tmp_path)
    task = service.store.create_knowledge_task(
        channel_id="C-FRONT",
        thread_ts="100.1",
        created_by="U-REQ",
        reason="test",
        drafted_title="Outlook WAM token cache fix",
        drafted_markdown="# Outlook WAM token cache fix\n",
    )
    monkeypatch.setattr(frontend_interactivity, "get_service", lambda: service)
    monkeypatch.setattr(frontend_interactivity.settings, "channel_create_knowledge", "C-CREATE")

    app = registered_app()
    client = fake_client()
    body = {
        "actions": [{"block_id": f"frontend_knowledge_assign:{task['id']}", "selected_user": "U-ASSIGNEE"}],
        "user": {"id": "U-MANAGER"},
        "channel": {"id": "C-CREATE"},
        "message": {"ts": "500.1", "text": "card", "blocks": build_knowledge_task_blocks(task)},
    }

    asyncio.run(app.actions[ASSIGN_KNOWLEDGE](AsyncMock(), body, client))

    client.conversations_open.assert_called_once_with(users="U-ASSIGNEE")
    dm_call = client.chat_postMessage.call_args_list[0]
    assert dm_call.kwargs["channel"] == "DM123"
    assert task["task_id"] in dm_call.kwargs["text"]
    assert "U-MANAGER" in dm_call.kwargs["text"]

    updated = service.store.get_knowledge_task(task["id"])
    assert updated["assignee_id"] == "U-ASSIGNEE"
    assert updated["status"] == "assigned"

    updated_blocks = client.chat_update.call_args.kwargs["blocks"]
    block_ids = [b.get("block_id", "") for b in updated_blocks]
    # Assignment alone doesn't close the card out; actions stay available.
    assert any(bid.startswith(KNOWLEDGE_ACTIONS_BLOCK_PREFIX) for bid in block_ids)


def test_dismiss_knowledge_marks_dismissed_and_removes_actions(tmp_path, monkeypatch):
    service = _confirmed_service(tmp_path)
    task = service.store.create_knowledge_task(
        channel_id="C-FRONT",
        thread_ts="100.1",
        created_by="U-REQ",
        reason="test",
        drafted_title="Outlook WAM token cache fix",
        drafted_markdown="# Outlook WAM token cache fix\n",
    )
    monkeypatch.setattr(frontend_interactivity, "get_service", lambda: service)

    app = registered_app()
    client = fake_client()
    body = {
        "actions": [{"value": json.dumps({"task_id": task["id"]})}],
        "user": {"id": "U-REVIEWER"},
        "channel": {"id": "C-CREATE"},
        "message": {"ts": "500.1", "text": "card", "blocks": build_knowledge_task_blocks(task)},
    }

    asyncio.run(app.actions[DISMISS_KNOWLEDGE](AsyncMock(), body, client))

    updated = service.store.get_knowledge_task(task["id"])
    assert updated["status"] == "dismissed"
    updated_blocks = client.chat_update.call_args.kwargs["blocks"]
    block_ids = [b.get("block_id", "") for b in updated_blocks]
    assert not any(bid.startswith(KNOWLEDGE_ACTIONS_BLOCK_PREFIX) for bid in block_ids)

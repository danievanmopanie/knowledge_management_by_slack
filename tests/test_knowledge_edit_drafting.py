from types import SimpleNamespace

import pytest

from src.bot import knowledge_edit_drafting as drafting
from src.knowledge.article_governance import ArticleGovernanceStore
from src.knowledge.edit_requests import EditRequestStore


class _Client:
    def __init__(self):
        self.updates = []

    async def chat_update(self, **kwargs):
        self.updates.append(kwargs)
        return {"ok": True}


def _task(store: EditRequestStore) -> dict:
    task = store.create_drafting(
        channel_id="C_FRONTEND",
        thread_ts="1.0",
        document_id="doc_1",
        document_title="Laptop Audio",
        base_version_id="v1",
        edit_note="Restart Windows Audio after reinstalling the driver",
        requested_by="U_TECH",
    )
    store.attach_shared_message(task["id"], channel_id="C_KNOWLEDGE", message_ts="2.0")
    return store.get(task["id"])


@pytest.mark.anyio
async def test_background_draft_updates_same_shared_card(tmp_path, monkeypatch):
    edit_store = EditRequestStore(tmp_path / "platform.db")
    governance = ArticleGovernanceStore(tmp_path / "platform.db")
    task = _task(edit_store)
    governance.assign_owner(
        document_id="doc_1",
        owner_user_id="U_OWNER",
        assigned_by_user_id="U_ADMIN",
    )

    monkeypatch.setattr(drafting, "_edit_store", edit_store)
    monkeypatch.setattr(drafting, "get_governance_store", lambda: governance)
    monkeypatch.setattr(drafting, "reconstruct_document_text", lambda document_id: "Old article")

    async def _revise(**kwargs):
        return "Updated article"

    monkeypatch.setattr(drafting, "revise_article", _revise)
    client = _Client()

    await drafting.draft_revision_task(client, task["id"])

    updated = edit_store.get(task["id"])
    assert updated["status"] == "review"
    assert updated["proposed_text"] == "Updated article"
    assert len(client.updates) == 1
    assert client.updates[0]["channel"] == "C_KNOWLEDGE"
    assert client.updates[0]["ts"] == "2.0"
    rendered = str(client.updates[0]["blocks"])
    assert "Proposed revision preview" in rendered
    assert "U_OWNER" in rendered


@pytest.mark.anyio
async def test_background_draft_failure_is_visible_and_does_not_hang(tmp_path, monkeypatch):
    edit_store = EditRequestStore(tmp_path / "platform.db")
    governance = ArticleGovernanceStore(tmp_path / "platform.db")
    task = _task(edit_store)

    monkeypatch.setattr(drafting, "_edit_store", edit_store)
    monkeypatch.setattr(drafting, "get_governance_store", lambda: governance)

    def _reconstruct(document_id):
        raise RuntimeError("vector store unavailable")

    monkeypatch.setattr(drafting, "reconstruct_document_text", _reconstruct)
    client = _Client()

    await drafting.draft_revision_task(client, task["id"])

    updated = edit_store.get(task["id"])
    assert updated["status"] == "draft_failed"
    assert "vector store unavailable" in updated["error_message"]
    assert len(client.updates) == 1
    rendered = str(client.updates[0]["blocks"])
    assert "Drafting failed" in rendered

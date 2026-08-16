"""Tests for bulk-onboarding an existing formal knowledge base and confirm-all staging."""

import asyncio
from pathlib import Path

from scripts import bulk_import_knowledge
from src.agents.knowledge_ingest import agent as knowledge_ingest_agent
from src.agents.knowledge_ingest.agent import KnowledgeIngestAgent
from src.core.audit import AuditStore
from src.core.context import RequestContext
from src.knowledge.staging import StagingStore


def _stage_local_file(
    staging: StagingStore, tmp_path: Path, *, name: str, text: str, uploader_id="U1", channel_id="C-KB"
) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return staging.create(
        slack_file_id=f"F-{name}",
        file_name=name,
        local_path=str(path),
        uploader_id=uploader_id,
        channel_id=channel_id,
    )


def _agent(tmp_path: Path) -> KnowledgeIngestAgent:
    agent = KnowledgeIngestAgent.__new__(KnowledgeIngestAgent)
    agent.staging = StagingStore(tmp_path / "platform.db")
    agent.audit = AuditStore(tmp_path / "platform.db")
    return agent


def test_list_staged_filters_by_uploader_and_channel(tmp_path):
    staging = StagingStore(tmp_path / "platform.db")
    _stage_local_file(staging, tmp_path, name="a.md", text="A", uploader_id="U1", channel_id="C-KB")
    _stage_local_file(staging, tmp_path, name="b.md", text="B", uploader_id="U2", channel_id="C-KB")
    _stage_local_file(staging, tmp_path, name="c.md", text="C", uploader_id="U1", channel_id="C-OTHER")

    staged = staging.list_staged(uploader_id="U1", channel_id="C-KB")

    assert [item["file_name"] for item in staged] == ["a.md"]


def test_confirm_all_commits_every_staged_upload_for_the_channel(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    committed = []

    def fake_commit_knowledge(*, text, title, source_id, owner_id):
        committed.append((title, source_id))
        return {"document_id": f"doc-{source_id}", "chunks": 2, "unchanged": False}

    monkeypatch.setattr(knowledge_ingest_agent, "commit_knowledge", fake_commit_knowledge)

    stage_a = _stage_local_file(agent.staging, tmp_path, name="a.md", text="Article A")
    stage_b = _stage_local_file(agent.staging, tmp_path, name="b.md", text="Article B")

    context = RequestContext.from_slack(channel_id="C-KB", user_id="U1")
    result = asyncio.run(agent.handle("confirm all", context))

    assert len(committed) == 2
    assert {title for title, _ in committed} == {"a.md", "b.md"}
    assert agent.staging.get(stage_a)["status"] == "confirmed"
    assert agent.staging.get(stage_b)["status"] == "confirmed"
    assert "Confirming 2 staged upload(s)" in result


def test_confirm_all_with_nothing_staged_says_so(tmp_path):
    agent = _agent(tmp_path)
    context = RequestContext.from_slack(channel_id="C-KB", user_id="U1")

    result = asyncio.run(agent.handle("confirm all", context))

    assert "no staged uploads" in result.lower()


def test_confirm_all_does_not_confirm_another_users_uploads(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    monkeypatch.setattr(
        knowledge_ingest_agent,
        "commit_knowledge",
        lambda **kwargs: {"document_id": "doc-x", "chunks": 1, "unchanged": False},
    )
    other_stage = _stage_local_file(agent.staging, tmp_path, name="a.md", text="Article A", uploader_id="U2")

    context = RequestContext.from_slack(channel_id="C-KB", user_id="U1")
    result = asyncio.run(agent.handle("confirm all", context))

    assert "no staged uploads" in result.lower()
    assert agent.staging.get(other_stage)["status"] == "staged"


def test_bulk_import_script_commits_new_files_and_skips_unchanged(tmp_path, monkeypatch):
    source = tmp_path / "existing-kb"
    source.mkdir()
    (source / "vpn-runbook.md").write_text("Reset the VPN client.", encoding="utf-8")
    (source / "printer-guide.txt").write_text("Reseat the USB cable.", encoding="utf-8")
    (source / "photo.png").write_bytes(b"not a real image, just bytes")

    calls = []

    def fake_commit_knowledge(*, text, title, source_id, owner_id):
        calls.append(source_id)
        unchanged = "vpn" in source_id
        return {"document_id": f"doc-{source_id}", "chunks": 1, "unchanged": unchanged}

    monkeypatch.setattr(bulk_import_knowledge, "commit_knowledge", fake_commit_knowledge)
    monkeypatch.setattr(bulk_import_knowledge.settings, "raw_docs_path", source)

    import sys

    monkeypatch.setattr(sys, "argv", ["bulk_import_knowledge.py"])
    bulk_import_knowledge.main()

    assert len(calls) == 2
    assert any("vpn-runbook" in c for c in calls)
    assert any("printer-guide" in c for c in calls)

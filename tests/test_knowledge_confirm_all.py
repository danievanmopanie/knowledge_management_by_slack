from pathlib import Path

import pytest

from src.agents.knowledge_ingest import agent as ingest_module
from src.agents.knowledge_ingest.agent import CONFIRM_ALL_MAX, KnowledgeIngestAgent
from src.core.audit import AuditStore
from src.core.context import RequestContext
from src.knowledge.staging import StagingStore


def _agent(tmp_path: Path) -> KnowledgeIngestAgent:
    agent = KnowledgeIngestAgent.__new__(KnowledgeIngestAgent)
    agent.staging = StagingStore(tmp_path / "platform.db")
    agent.audit = AuditStore(tmp_path / "platform.db")
    return agent


def _stage(
    agent: KnowledgeIngestAgent,
    tmp_path: Path,
    index: int,
    *,
    uploader_id: str = "U1",
    channel_id: str = "C_KB",
) -> str:
    path = tmp_path / f"article-{index}.md"
    path.write_text(f"Knowledge article {index}", encoding="utf-8")
    return agent.staging.create(
        slack_file_id=f"F{index}",
        file_name=path.name,
        local_path=str(path),
        uploader_id=uploader_id,
        channel_id=channel_id,
    )


@pytest.mark.anyio
async def test_confirm_all_confirms_up_to_five_owned_uploads_in_current_channel(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    stage_ids = [_stage(agent, tmp_path, index) for index in range(CONFIRM_ALL_MAX)]
    calls = []

    def fake_commit_knowledge(**kwargs):
        calls.append(kwargs["source_id"])
        return {
            "document_id": f"doc-{kwargs['source_id']}",
            "chunks": 1,
            "unchanged": False,
        }

    monkeypatch.setattr(ingest_module, "commit_knowledge", fake_commit_knowledge)
    context = RequestContext.from_slack(channel_id="C_KB", user_id="U1")

    result = await agent.handle("confirm all", context)

    assert len(calls) == CONFIRM_ALL_MAX
    assert f"{CONFIRM_ALL_MAX} confirmed, 0 failed" in result
    assert all(agent.staging.get(stage_id)["status"] == "confirmed" for stage_id in stage_ids)


@pytest.mark.anyio
async def test_confirm_all_is_scoped_to_current_user_and_channel(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    owned = _stage(agent, tmp_path, 1, uploader_id="U1", channel_id="C_KB")
    other_user = _stage(agent, tmp_path, 2, uploader_id="U2", channel_id="C_KB")
    other_channel = _stage(agent, tmp_path, 3, uploader_id="U1", channel_id="C_OTHER")

    monkeypatch.setattr(
        ingest_module,
        "commit_knowledge",
        lambda **kwargs: {
            "document_id": f"doc-{kwargs['source_id']}",
            "chunks": 1,
            "unchanged": False,
        },
    )
    context = RequestContext.from_slack(channel_id="C_KB", user_id="U1")

    result = await agent.handle("confirm all", context)

    assert "1 confirmed, 0 failed" in result
    assert agent.staging.get(owned)["status"] == "confirmed"
    assert agent.staging.get(other_user)["status"] == "staged"
    assert agent.staging.get(other_channel)["status"] == "staged"


@pytest.mark.anyio
async def test_confirm_all_refuses_six_or_more_without_partial_commit(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    stage_ids = [_stage(agent, tmp_path, index) for index in range(CONFIRM_ALL_MAX + 1)]
    calls = []

    def fake_commit_knowledge(**kwargs):
        calls.append(kwargs)
        return {"document_id": "doc", "chunks": 1, "unchanged": False}

    monkeypatch.setattr(ingest_module, "commit_knowledge", fake_commit_knowledge)
    context = RequestContext.from_slack(channel_id="C_KB", user_id="U1")

    result = await agent.handle("confirm all", context)

    assert f"capped at {CONFIRM_ALL_MAX}" in result
    assert "bulk-import CLI" in result
    assert calls == []
    assert all(agent.staging.get(stage_id)["status"] == "staged" for stage_id in stage_ids)


@pytest.mark.anyio
async def test_confirm_all_with_nothing_staged_is_clear(tmp_path):
    agent = _agent(tmp_path)
    context = RequestContext.from_slack(channel_id="C_KB", user_id="U1")

    result = await agent.handle("confirm all", context)

    assert "no staged uploads" in result.lower()

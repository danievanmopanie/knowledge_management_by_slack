import pytest

from src.bot.frontend_knowledge_edit import (
    _choice_blocks,
    has_actionable_edit_detail,
    looks_like_knowledge_edit,
    offer_knowledge_edit,
)
from src.knowledge.citation_memory import CitationMemory


def test_edit_intent_is_fast_and_specific():
    assert looks_like_knowledge_edit("That knowledge article is outdated")
    assert looks_like_knowledge_edit("Update the KB article: restart Windows Audio after reinstalling the driver")
    assert looks_like_knowledge_edit("This knowledge is incomplete. Do not invent replication steps when the exact approved procedure is not documented. The article should say that the replication procedure must be confirmed by the resolver before technicians execute it.")
    assert looks_like_knowledge_edit("The article is incomplete and needs a confirmed procedure")
    assert not looks_like_knowledge_edit("My laptop audio is not working")


def test_vague_edit_request_does_not_trigger_drafting():
    assert not has_actionable_edit_detail("That knowledge article is outdated")
    assert has_actionable_edit_detail(
        "Update the knowledge article: after reinstalling the driver, restart Windows Audio"
    )


def test_choice_blocks_use_unique_action_ids_for_multiple_articles():
    blocks = _choice_blocks(
        citations=[
            {"document_id": "D1", "title": "Babylon employee profile deletion"},
            {"document_id": "D2", "title": "Babylon employee profile deletion"},
        ],
        edit_note="This knowledge is incomplete and should require resolver confirmation.",
        channel_id="C_FRONT",
        thread_ts="T1",
    )
    action_ids = [element["action_id"] for element in blocks[1]["elements"]]
    assert len(action_ids) == len(set(action_ids))


def test_citation_memory_replaces_with_small_ranked_set(tmp_path):
    memory = CitationMemory(tmp_path / "platform.db")
    memory.replace(
        channel_id="C1",
        thread_ts="T1",
        articles=[
            {"document_id": "D1", "title": "Audio fix"},
            {"document_id": "D2", "title": "Driver guide"},
            {"document_id": "D1", "title": "Audio fix duplicate"},
            {"document_id": "D3", "title": "Windows services"},
            {"document_id": "D4", "title": "Ignored fourth"},
        ],
    )
    assert [item["document_id"] for item in memory.recent("C1", "T1")] == ["D1", "D2", "D3"]


class _Client:
    def __init__(self):
        self.posts = []

    async def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {"ok": True, "ts": "1.2"}


@pytest.mark.anyio
async def test_vague_edit_request_asks_for_specific_correction_without_llm(monkeypatch):
    client = _Client()
    handled = await offer_knowledge_edit(
        client,
        channel_id="C_FRONT",
        thread_ts="T1",
        edit_note="That knowledge article is outdated",
    )
    assert handled is True
    assert len(client.posts) == 1
    assert "specific correction" in client.posts[0]["text"]

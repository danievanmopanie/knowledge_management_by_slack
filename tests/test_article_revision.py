"""Tests for LLM-assisted revision of an existing knowledge article."""

import asyncio

from langchain_core.documents import Document

from src.knowledge.article_revision import reconstruct_document_text, revise_article


class FakeVectorStore:
    def __init__(self, docs):
        self._docs = docs

    def all_documents(self, where=None):
        return self._docs


class FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.received_messages = None

    async def ainvoke(self, messages):
        self.received_messages = messages

        class Response:
            content = self.content

        return Response()


class FailingLLM:
    async def ainvoke(self, messages):
        raise RuntimeError("model unavailable")


def test_reconstruct_document_text_orders_chunks_by_index():
    docs = [
        Document(page_content="Second part.", metadata={"chunk_index": 1}),
        Document(page_content="First part.", metadata={"chunk_index": 0}),
    ]

    text = reconstruct_document_text("doc_1", vector_store=FakeVectorStore(docs))

    assert text == "First part.\n\nSecond part."


def test_reconstruct_document_text_handles_no_chunks():
    assert reconstruct_document_text("doc_missing", vector_store=FakeVectorStore([])) == ""


def test_revise_article_applies_the_requested_correction():
    llm = FakeLLM("# VPN Runbook\n\nReset the VPN client. Also clear the cached token first.")

    revised = asyncio.run(
        revise_article(
            current_text="# VPN Runbook\n\nReset the VPN client.",
            edit_note="Also clear the cached token first.",
            llm=llm,
        )
    )

    assert "clear the cached token" in revised
    human_message = llm.received_messages[1]
    assert "Reset the VPN client." in human_message.content
    assert "Also clear the cached token first." in human_message.content


def test_revise_article_falls_back_to_original_with_reviewer_note_on_failure():
    revised = asyncio.run(
        revise_article(
            current_text="# VPN Runbook\n\nReset the VPN client.",
            edit_note="This is wrong somehow.",
            llm=FailingLLM(),
        )
    )

    assert "Reset the VPN client." in revised
    assert "## Reviewer note" in revised
    assert "This is wrong somehow." in revised

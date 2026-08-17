"""Tests for gen-AI knowledge article drafting."""

import asyncio

from src.knowledge.article_drafting import draft_knowledge_article


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


def test_draft_extracts_title_and_keeps_structured_markdown():
    llm = FakeLLM(
        "# Outlook repeatedly prompts for credentials\n\n"
        "## Symptom\nOutlook keeps asking for a password.\n\n"
        "## Root cause\nCorrupted WAM token cache.\n\n"
        "## Resolution steps\n1. Clear the WAM token cache.\n2. Restart Outlook.\n"
    )

    article = asyncio.run(
        draft_knowledge_article(
            symptom="Outlook keeps asking for a password",
            resolution="Clear the WAM token cache",
            contributors=["U1", "U2"],
            related_incidents=["INC0012345"],
            fallback_title="fallback",
            llm=llm,
        )
    )

    assert article.title == "Outlook repeatedly prompts for credentials"
    assert "## Resolution steps" in article.markdown
    assert "1. Clear the WAM token cache." in article.markdown
    # Evidence actually supplied to the model, not invented later.
    human_message = llm.received_messages[1]
    assert "Outlook keeps asking for a password" in human_message.content
    assert "INC0012345" in human_message.content


def test_draft_falls_back_to_honest_skeleton_on_llm_failure():
    article = asyncio.run(
        draft_knowledge_article(
            symptom="VPN will not connect",
            resolution="Reset the VPN profile",
            fallback_title="VPN connection failure",
            llm=FailingLLM(),
        )
    )

    assert article.title == "VPN connection failure"
    assert "Root cause not confirmed" in article.markdown
    assert "Reset the VPN profile" in article.markdown


def test_draft_uses_fallback_title_when_model_omits_a_heading():
    llm = FakeLLM("Some free-form text without a title heading.")

    article = asyncio.run(
        draft_knowledge_article(
            symptom="Printer offline",
            resolution="Reseated the USB cable",
            fallback_title="Printer offline fix",
            llm=llm,
        )
    )

    assert article.title == "Printer offline fix"

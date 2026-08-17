"""Verify Frontend Support stays evidence-led publicly but can coach privately."""

import asyncio

from src.agents.frontend_support.agent import (
    FrontendSupportAgent,
    GENERAL_GUIDANCE_PREFIX,
    INSUFFICIENT_EVIDENCE_RESPONSE,
)
from src.core.context import RequestContext
from src.knowledge.retrieval_models import RetrievalResult


class EmptyRetriever:
    def search(self, request):
        return RetrievalResult(
            query=request.text,
            candidates=[],
            confidence_score=0.0,
            evidence_level="insufficient",
        )


class EmptyIncidentRAG:
    def build_context(self, message, k=5, **kwargs):
        return ""


class MustNotRunLLM:
    async def ainvoke(self, messages):
        raise AssertionError("LLM should not run for proactive public support when evidence is insufficient")


class GeneralGuidanceLLM:
    async def ainvoke(self, messages):
        class Response:
            content = "WAM is the Windows Web Account Manager. Start by confirming the exact sign-in error."

        return Response()


def test_frontend_abstains_before_llm_call():
    agent = FrontendSupportAgent.__new__(FrontendSupportAgent)
    agent.retriever = EmptyRetriever()
    agent.incident_rag = EmptyIncidentRAG()
    agent.llm = MustNotRunLLM()
    context = RequestContext.from_slack(channel_id="C1", user_id="U1")

    answer = asyncio.run(agent.handle("something with no evidence", context))

    assert answer == INSUFFICIENT_EVIDENCE_RESPONSE


def test_private_coach_can_use_labeled_general_guidance_without_internal_evidence():
    agent = FrontendSupportAgent.__new__(FrontendSupportAgent)
    agent.retriever = EmptyRetriever()
    agent.incident_rag = EmptyIncidentRAG()
    agent.llm = GeneralGuidanceLLM()
    context = RequestContext.from_slack(channel_id="D1", user_id="U1")

    answer = asyncio.run(
        agent.handle(
            "PRIVATE COACHING SESSION. I don't understand WAM; explain it to me.",
            context,
        )
    )

    assert answer.startswith(GENERAL_GUIDANCE_PREFIX)
    assert "Windows Web Account Manager" in answer

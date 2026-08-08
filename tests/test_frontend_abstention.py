"""Verify Frontend Support does not ask the LLM to guess without evidence."""

import asyncio

from src.agents.frontend_support.agent import FrontendSupportAgent, INSUFFICIENT_EVIDENCE_RESPONSE
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
    def build_context(self, message, k=5):
        return ""


class MustNotRunLLM:
    async def ainvoke(self, messages):
        raise AssertionError("LLM should not run when evidence is insufficient")


def test_frontend_abstains_before_llm_call():
    agent = FrontendSupportAgent.__new__(FrontendSupportAgent)
    agent.retriever = EmptyRetriever()
    agent.incident_rag = EmptyIncidentRAG()
    agent.llm = MustNotRunLLM()
    context = RequestContext.from_slack(channel_id="C1", user_id="U1")

    answer = asyncio.run(agent.handle("something with no evidence", context))

    assert answer == INSUFFICIENT_EVIDENCE_RESPONSE

"""Verify Frontend Support can ask a natural clarifying follow-up question."""

import asyncio

from src.agents.frontend_support.agent import (
    FrontendSupportAgent,
    is_clarifying_question,
    strip_clarification_marker,
)
from src.core.context import RequestContext
from src.knowledge.support_evidence import SupportEvidencePackage


class FakeEvidenceService:
    def __init__(self, package: SupportEvidencePackage):
        self.package = package

    def build(self, message, context, limit=5):
        return self.package


class ClarifyLLM:
    async def ainvoke(self, messages):
        class Response:
            content = "CLARIFY: What is the exact device model and OS version?"

        return Response()


class NormalLLM:
    async def ainvoke(self, messages):
        class Response:
            content = "Try clearing the WAM token cache and restarting Outlook."

        return Response()


def _weak_evidence_package() -> SupportEvidencePackage:
    return SupportEvidencePackage(
        query="laptop is broken",
        incident_context="### Similar past incidents\n[1] INC0000001 | some partial match",
    )


def test_agent_asks_clarifying_question_and_marks_it_invisibly():
    agent = FrontendSupportAgent.__new__(FrontendSupportAgent)
    agent.evidence = FakeEvidenceService(_weak_evidence_package())
    agent.llm = ClarifyLLM()
    context = RequestContext.from_slack(channel_id="C1", user_id="U1")

    answer = asyncio.run(agent.handle("A laptop is not working properly", context))

    assert is_clarifying_question(answer)
    visible = strip_clarification_marker(answer)
    assert visible == "What is the exact device model and OS version?"
    assert "CLARIFY:" not in visible
    assert "Evidence" not in visible


def test_agent_normal_answer_is_not_marked_as_clarifying():
    agent = FrontendSupportAgent.__new__(FrontendSupportAgent)
    agent.evidence = FakeEvidenceService(_weak_evidence_package())
    agent.llm = NormalLLM()
    context = RequestContext.from_slack(channel_id="C1", user_id="U1")

    answer = asyncio.run(agent.handle("A laptop is not working properly", context))

    assert not is_clarifying_question(answer)
    assert strip_clarification_marker(answer) == answer


def test_strip_clarification_marker_is_a_no_op_on_plain_text():
    assert strip_clarification_marker("just a normal answer") == "just a normal answer"
    assert is_clarifying_question("just a normal answer") is False

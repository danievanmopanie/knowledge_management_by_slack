import asyncio
from types import SimpleNamespace

from src.agents.frontend_support.agent import FrontendSupportAgent
from src.core.context import RequestContext


def test_trusted_pattern_lane_does_not_invoke_response_llm():
    trusted_response = (
        "We have *5 trusted incidents* matching *Network switch offline*.\n\n"
        "*What has worked before*\n"
        "• *Restore electrical power to network equipment* — 3 of 5 trusted incidents — "
        "INC0022194, INC0022195, INC0022253\n"
        "• *Reconnect network switch* — 2 of 5 trusted incidents — INC0022228, INC0022229\n\n"
        "*Best-supported next step*\n"
        "First verify whether the current incident is consistent with the leading recorded path: "
        "*Restore electrical power to network equipment*.\n\n"
        "*Root-cause evidence*\n"
        "The current evidence does not support one universal root cause.\n\n"
        "*Failed paths*\n"
        "No repeated failed troubleshooting path has been established for this pattern."
    )
    package = SimpleNamespace(
        exact_lookup_requested=False,
        exact_incident_numbers=(),
        exact_incident_context="",
        organisational_context="trusted pattern context",
        trusted_pattern_response=trusted_response,
        has_evidence=True,
        governed_should_answer=False,
        governed_candidates=[],
    )

    class Evidence:
        def build(self, message, context, limit):
            return package

    class BombLLM:
        async def ainvoke(self, messages):
            raise AssertionError("response LLM must not be called for trusted pattern serving")

        async def astream(self, messages):
            raise AssertionError("response LLM must not be streamed for trusted pattern serving")
            yield  # pragma: no cover

    agent = FrontendSupportAgent.__new__(FrontendSupportAgent)
    agent.evidence = Evidence()
    agent.llm = BombLLM()

    answer = asyncio.run(
        agent.handle(
            "A network switch is offline and users cannot connect. What should I check?",
            RequestContext.from_slack(channel_id="LOCAL_TEST", user_id="U1"),
        )
    )

    assert answer == trusted_response
    assert "3 of 5 trusted incidents" in answer
    assert "2 of 5 trusted incidents" in answer
    assert "60%" not in answer
    assert "uplink" not in answer.lower()
    assert "surge protector" not in answer.lower()
    assert "INC0067217" not in answer
    assert "INC0085733" not in answer

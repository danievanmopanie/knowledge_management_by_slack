import asyncio
from types import SimpleNamespace

from src.agents.frontend_support.agent import FrontendSupportAgent, _render_exact_incident_brief
from src.core.context import RequestContext


def _case():
    return SimpleNamespace(
        number="INC0092846",
        current={
            "short_description": "64183602 - Profile Deleted on Babylon",
            "resolution_notes": (
                "Incident Issue: Force Replications\n"
                "Steps Taken to Identify the Issue: Checked SAP Master Data, TNA Personnel, "
                "Babylon.PersonnelInformation, Table_Pers\n"
                "Steps Taken to Resolve the Issue: Employees were replicated and surfaced on site"
            ),
            "state": "Closed",
            "assignment_group": "PT-IM-APP-T&A-PS",
            "assigned_to": "William Mashaba",
            "location": "Mogalakwena Complex",
        },
    )


def _enriched():
    return SimpleNamespace(
        incident_number="INC0092846",
        symptom="Employee profile missing in Babylon",
        resolution="Employees were replicated and surfaced on site",
        resolution_pattern="Replicate Babylon employee profiles",
        root_cause="",
        root_cause_pattern="",
        pattern_key="babylon-employee-profile-deletion",
        actions=(
            SimpleNamespace(
                action="Checked SAP Master Data, TNA Personnel, Babylon.PersonnelInformation",
                canonical_action="Verify employee data in SAP and Babylon",
                outcome="unknown",
                confidence=0.80,
            ),
            SimpleNamespace(
                action="Employees were replicated and surfaced on site",
                canonical_action="Replicate employee profiles in Babylon",
                outcome="successful",
                confidence=0.90,
            ),
        ),
    )


def test_exact_brief_preserves_uncertainty_and_single_case_boundary():
    answer = _render_exact_incident_brief(
        _case(),
        _enriched(),
        {"incident_count": 1},
    )

    assert "Employees were replicated and surfaced on site" in answer
    assert "Verify employee data in SAP and Babylon" in answer
    assert "No confirmed root cause is recorded" in answer
    assert "supported by this incident alone" in answer
    assert "replication gap" not in answer.lower()
    assert "manual deletion" not in answer.lower()
    assert "proven fix" not in answer.lower()
    assert "No need to verify SAP" not in answer


def test_exact_lookup_does_not_invoke_response_llm():
    package = SimpleNamespace(
        exact_lookup_requested=True,
        exact_incident_numbers=("INC0092846",),
        exact_incident_context="raw exact context",
        organisational_context="enriched exact context",
        trusted_pattern_response="",
        has_evidence=True,
    )

    class CaseFiles:
        def get(self, number):
            assert number == "INC0092846"
            return _case()

    class Store:
        def get(self, number):
            assert number == "INC0092846"
            return _enriched()

        def get_pattern(self, key):
            assert key == "babylon-employee-profile-deletion"
            return {"incident_count": 1}

    class Organisational:
        store = Store()

    class Evidence:
        casefiles = CaseFiles()
        organisational = Organisational()

        def build(self, message, context, limit):
            return package

    class BombLLM:
        async def ainvoke(self, messages):
            raise AssertionError("response LLM must not be called for exact incident lookup")

        async def astream(self, messages):
            raise AssertionError("response LLM must not be streamed for exact incident lookup")
            yield  # pragma: no cover

    agent = FrontendSupportAgent.__new__(FrontendSupportAgent)
    agent.evidence = Evidence()
    agent.llm = BombLLM()

    answer = asyncio.run(
        agent.handle(
            "Can anyone check what happened with INC0092846 and how it was resolved?",
            RequestContext.from_slack(channel_id="LOCAL_TEST", user_id="U1"),
        )
    )

    assert "INC0092846" in answer
    assert "Employees were replicated and surfaced on site" in answer
    assert "No confirmed root cause is recorded" in answer

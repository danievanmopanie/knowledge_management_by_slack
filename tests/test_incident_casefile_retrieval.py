from datetime import datetime, timezone

from langchain_core.documents import Document

from src.agents.frontend_support.conversation import looks_like_support
from src.core.context import RequestContext
from src.knowledge.fast_temporal_graph import FastTemporalGraphBuilder
from src.knowledge.graphstore import GraphStore
from src.knowledge.incident_casefile import (
    IncidentCaseFileStore,
    extract_incident_numbers,
    looks_like_exact_incident_lookup,
)
from src.knowledge.retrieval_models import RetrievalResult
from src.knowledge.support_evidence import SupportEvidenceService
from src.knowledge.temporal_incidents import TemporalIncidentStore
from src.reporting.incidents import Incident


def _seed_case(tmp_path):
    db_path = tmp_path / "platform.db"
    graph = GraphStore(path=tmp_path / "graph")
    incident = Incident(
        number="INC0092846",
        short_description="Outlook repeatedly prompts for credentials",
        description="User is prompted for a password every time Outlook opens.",
        work_notes="Cleared stale Office credentials, restarted Outlook, then validated sign-in with the user.",
        resolution_notes="Removed the stale cached Office credential. Outlook accepted the new password and mail synchronised normally.",
        state="Closed",
        priority="3 - Moderate",
        assignment_group="PT-IM-FLS-Oxford",
        assigned_to="Technician One",
        location="144 Oxford Rd Jhb",
        configuration_item="LAPTOP-001",
        opened_at=datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 1, 9, 15, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 8, 1, 9, 10, tzinfo=timezone.utc),
        closed_at=datetime(2026, 8, 2, 9, 10, tzinfo=timezone.utc),
        category="Application",
        subcategory="Outlook",
    )
    TemporalIncidentStore(db_path).ingest_snapshot(
        [incident],
        source_file="incident (Aug_1).csv",
        observed_at="2026-08-01T10:00:00+00:00",
        complete_snapshot=False,
    )
    FastTemporalGraphBuilder(graph).build(
        [incident],
        observed_at="2026-08-01T10:00:00+00:00",
        source_file="incident (Aug_1).csv",
    )
    return db_path, graph


def test_exact_case_file_contains_real_resolution_and_graph(tmp_path):
    db_path, graph = _seed_case(tmp_path)
    store = IncidentCaseFileStore(path=db_path, graph_store=graph)

    case = store.get("inc0092846")
    assert case is not None
    rendered = store.render_exact(case)

    assert "INC0092846" in rendered
    assert "Outlook repeatedly prompts for credentials" in rendered
    assert "Cleared stale Office credentials" in rendered
    assert "Removed the stale cached Office credential" in rendered
    assert "PT-IM-FLS-Oxford" in rendered
    assert "144 Oxford Rd Jhb" in rendered
    assert "assigned_to_group" in rendered


def test_exact_lookup_bypasses_vector_and_governed_search(tmp_path):
    db_path, graph = _seed_case(tmp_path)

    class MustNotSearchRetriever:
        def search(self, request):
            raise AssertionError("governed/vector retrieval must not run for an exact incident lookup")

    class MustNotSearchIncidentRAG:
        graph_store = graph

        def similar_incidents(self, query, k=3):
            raise AssertionError("semantic incident search must not run for an exact incident lookup")

    service = SupportEvidenceService(
        retriever=MustNotSearchRetriever(),
        incident_rag=MustNotSearchIncidentRAG(),
        casefiles=IncidentCaseFileStore(path=db_path, graph_store=graph),
    )
    context = RequestContext.from_slack(channel_id="C-FRONT", user_id="U1")

    package = service.build(
        "Can anyone check what happened with INC0092846 and how it was resolved?",
        context,
    )

    assert package.exact_lookup_requested is True
    assert package.exact_incident_numbers == ("INC0092846",)
    assert "Removed the stale cached Office credential" in package.exact_incident_context
    assert not package.exact_incident_missing


def test_semantic_hit_is_hydrated_with_full_case_resolution(tmp_path):
    db_path, graph = _seed_case(tmp_path)

    class EmptyGovernedRetriever:
        def search(self, request):
            return RetrievalResult(query=request.text, evidence_level="insufficient")

    class ProblemOnlyIncidentRAG:
        graph_store = graph

        def similar_incidents(self, query, k=3):
            return [
                Document(
                    page_content="Problem-only vector chunk; no resolution text here.",
                    metadata={
                        "number": "INC0092846",
                        "field_group": "problem",
                        "matched_fields": "problem",
                        "score": 0.91,
                    },
                )
            ]

    service = SupportEvidenceService(
        retriever=EmptyGovernedRetriever(),
        incident_rag=ProblemOnlyIncidentRAG(),
        casefiles=IncidentCaseFileStore(path=db_path, graph_store=graph),
    )
    context = RequestContext.from_slack(channel_id="C-FRONT", user_id="U1")

    package = service.build("Outlook keeps asking for the new password", context)

    assert package.exact_lookup_requested is False
    assert "Problem-only vector chunk" not in package.incident_context
    assert "Removed the stale cached Office credential" in package.incident_context
    assert "Cleared stale Office credentials" in package.incident_context


def test_incident_reference_is_support_trigger_without_mention():
    text = "Can anyone check what happened with INC0092846 and how it was resolved?"

    assert looks_like_support(text) is True
    assert extract_incident_numbers(text) == ["INC0092846"]
    assert looks_like_exact_incident_lookup(text) is True

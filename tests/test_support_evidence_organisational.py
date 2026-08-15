from langchain_core.documents import Document

from src.core.context import RequestContext
from src.knowledge.retrieval_models import RetrievalResult
from src.knowledge.support_evidence import SupportEvidenceService


class _EmptyGoverned:
    def search(self, request):
        return RetrievalResult(query=request.text, evidence_level="insufficient")


class _RawIncidentRag:
    graph_store = None

    def similar_incidents(self, query, k=3):
        return [
            Document(
                page_content="raw hit",
                metadata={"number": "INC0011111", "score": 0.8, "matched_fields": "problem"},
            )
        ]


class _NoRawIncidentRag:
    graph_store = None

    def similar_incidents(self, query, k=3):
        raise AssertionError("exact incident lookup must not use semantic raw incident search")


class _EmptyGraph:
    def related(self, entity, depth=1):
        return []


class _EmptyCaseFiles:
    def get(self, number):
        return None


class _Organisational:
    def exact_context(self, number):
        if number == "INC0092846":
            return "### Enriched support knowledge — INC0092846\nResolution pattern: Clear cached Office credentials"
        return ""

    def collective_context(self, query, **kwargs):
        return (
            "### Organisational pattern evidence\n"
            "Retrieved enriched incidents: 8\n"
            "Observed resolutions:\n"
            "- Clear cached Office credentials: 6 incident(s) — INC1, INC2"
        )


def _context():
    return RequestContext.from_slack(channel_id="C-FRONT", user_id="U1")


def test_exact_incident_can_be_answered_from_enriched_knowledge_without_vector_search():
    service = SupportEvidenceService(
        retriever=_EmptyGoverned(),
        incident_rag=_NoRawIncidentRag(),
        support_graph=_EmptyGraph(),
        casefiles=_EmptyCaseFiles(),
        organisational=_Organisational(),
    )

    package = service.build(
        "What happened with INC0092846 and how was it resolved?",
        _context(),
    )

    assert package.exact_lookup_requested is True
    assert "Clear cached Office credentials" in package.organisational_context
    assert package.has_evidence is True


def test_normal_support_query_includes_deterministic_organisational_pattern_context():
    service = SupportEvidenceService(
        retriever=_EmptyGoverned(),
        incident_rag=_RawIncidentRag(),
        support_graph=_EmptyGraph(),
        casefiles=_EmptyCaseFiles(),
        organisational=_Organisational(),
    )

    package = service.build(
        "Outlook keeps asking for the password after a reset",
        _context(),
    )

    assert "Retrieved enriched incidents: 8" in package.organisational_context
    assert "Clear cached Office credentials: 6 incident(s)" in package.organisational_context
    assert package.has_evidence is True

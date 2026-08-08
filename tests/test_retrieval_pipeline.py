"""Tests for the typed governed retrieval pipeline."""

from langchain_core.documents import Document

from src.core.context import RequestContext
from src.knowledge.retrieval_models import RetrievalQuery
from src.knowledge.retriever import HybridRetriever


class FakeVectorStore:
    def __init__(self, documents):
        self.documents = documents

    def similarity_search(self, query, k=5, where=None):
        return self.documents[:k]


class FakeGraphStore:
    def search_entities(self, query, limit=5):
        return []

    def get_related(self, entity, max_depth=1):
        return []


class FakeAuditStore:
    def __init__(self):
        self.events = []

    def record(self, context, **event):
        self.events.append((context, event))


def _retriever(documents):
    return HybridRetriever(
        vector_store=FakeVectorStore(documents),
        graph_store=FakeGraphStore(),
        audit_store=FakeAuditStore(),
    )


def test_search_preserves_order_and_stable_evidence_ids():
    documents = [
        Document(page_content="First", metadata={"chunk_id": "chunk-1", "source": "a.md"}),
        Document(page_content="Second", metadata={"chunk_id": "chunk-2", "source": "b.md"}),
    ]
    result = _retriever(documents).search(RetrievalQuery(text="vpn", limit=2))

    assert result.evidence_ids == ("chunk-1", "chunk-2")
    assert [candidate.content for candidate in result.candidates] == ["First", "Second"]


def test_search_returns_empty_result_without_candidates():
    result = _retriever([]).search(RetrievalQuery(text="nothing", limit=5))

    assert result.candidates == []
    assert result.documents == []
    assert result.to_context_string() == ""


def test_search_filters_restricted_content_before_returning_evidence():
    documents = [
        Document(
            page_content="Secret",
            metadata={
                "chunk_id": "secret-1",
                "visibility": "restricted",
                "allowed_user_ids": "U-ALLOWED",
            },
        ),
        Document(
            page_content="General",
            metadata={"chunk_id": "general-1", "visibility": "internal"},
        ),
    ]
    context = RequestContext.from_slack(channel_id="C1", user_id="U-DENIED")
    result = _retriever(documents).search(
        RetrievalQuery(text="support", context=context, limit=5)
    )

    assert result.evidence_ids == ("general-1",)
    assert all(candidate.content != "Secret" for candidate in result.candidates)

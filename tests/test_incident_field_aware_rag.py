"""Tests for field-aware historical incident embeddings and aggregation."""

from langchain_core.documents import Document

from src.knowledge.incident_rag import (
    IncidentRAG,
    _aggregate_incident_hits,
    _field_document,
)
from src.reporting.incidents import Incident


class FakeVectorStore:
    def __init__(self, search_docs=None):
        self.search_docs = search_docs or []
        self.added_documents = []
        self.added_metadatas = []
        self.added_ids = []
        self.deleted_ids = []
        self.last_search_k = None

    def count(self):
        return len(self.added_ids)

    def delete_documents(self, ids):
        self.deleted_ids.extend(ids)

    def add_documents(self, documents, metadatas=None, ids=None, batch_size=None):
        self.added_documents.extend(documents)
        self.added_metadatas.extend(metadatas or [])
        self.added_ids.extend(ids or [])
        return ids or []

    def similarity_search(self, query, k=5, where=None):
        self.last_search_k = k
        return self.search_docs[:k]


class FakeGraphStore:
    def __init__(self):
        self.entities = []
        self.relations = []
        self.saved = False

    def add_entity(self, entity_id, entity_type, **properties):
        self.entities.append((entity_id, entity_type, properties))

    def add_relation(self, source, target, relation, **properties):
        self.relations.append((source, target, relation, properties))

    def save(self):
        self.saved = True

    def get_related(self, entity_id, max_depth=1):
        return []


def _incident():
    return Incident(
        number="INC0012345",
        short_description="Outlook repeatedly prompts for credentials",
        description="User cannot open Outlook because authentication repeats.",
        work_notes="Rebuilt the Outlook profile but the prompt returned.",
        comments="User confirmed the issue persisted after reboot.",
        resolution_notes="Cleared the WAM token cache and Outlook opened normally.",
        state="Resolved",
        assignment_group="Frontend Support",
        assigned_to="Jane Tech",
        location="Johannesburg",
    )


def test_field_documents_keep_problem_troubleshooting_and_resolution_separate():
    inc = _incident()

    problem = _field_document(inc, "problem")
    troubleshooting = _field_document(inc, "troubleshooting")
    resolution = _field_document(inc, "resolution")

    assert "Outlook repeatedly prompts" in problem
    assert "Rebuilt the Outlook profile" not in problem
    assert "Rebuilt the Outlook profile" in troubleshooting
    assert "Cleared the WAM token cache" not in troubleshooting
    assert "Cleared the WAM token cache" in resolution
    assert "Outlook repeatedly prompts" not in resolution


def test_index_incident_creates_three_stable_field_documents():
    vector = FakeVectorStore()
    graph = FakeGraphStore()
    rag = IncidentRAG(vector_store=vector, graph_store=graph)

    result = rag.index_incidents([_incident()], force=True)

    assert result["indexed"] == 1
    assert result["indexed_documents"] == 3
    assert vector.added_ids == [
        "incident-INC0012345-problem",
        "incident-INC0012345-troubleshooting",
        "incident-INC0012345-resolution",
    ]
    assert [meta["field_group"] for meta in vector.added_metadatas] == [
        "problem",
        "troubleshooting",
        "resolution",
    ]
    assert "incident-INC0012345" in vector.deleted_ids
    assert graph.saved is True


def test_field_hits_are_aggregated_to_one_result_per_incident():
    raw = [
        Document(
            page_content="Resolution notes: Clear WAM token cache",
            metadata={
                "number": "INC1",
                "field_group": "resolution",
                "score": 0.93,
                "state": "Resolved",
            },
        ),
        Document(
            page_content="Description: Outlook credential prompt",
            metadata={
                "number": "INC1",
                "field_group": "problem",
                "score": 0.88,
                "state": "Resolved",
            },
        ),
        Document(
            page_content="Description: Printer queue failure",
            metadata={
                "number": "INC2",
                "field_group": "problem",
                "score": 0.84,
                "state": "Resolved",
            },
        ),
    ]

    results = _aggregate_incident_hits(raw, k=5)

    assert len(results) == 2
    assert results[0].metadata["number"] == "INC1"
    assert results[0].metadata["score"] == 0.93
    assert results[0].metadata["matched_fields"] == "problem,resolution"
    assert "Problem match" in results[0].page_content
    assert "Resolution match" in results[0].page_content


def test_similarity_search_fetches_extra_field_hits_then_returns_incidents():
    raw = [
        Document(
            page_content="Description: Outlook prompt",
            metadata={"number": "INC1", "field_group": "problem", "score": 0.9},
        ),
        Document(
            page_content="Resolution notes: Clear token cache",
            metadata={"number": "INC1", "field_group": "resolution", "score": 0.85},
        ),
        Document(
            page_content="Description: Teams sign-in issue",
            metadata={"number": "INC2", "field_group": "problem", "score": 0.8},
        ),
    ]
    vector = FakeVectorStore(search_docs=raw)
    rag = IncidentRAG(vector_store=vector, graph_store=FakeGraphStore())

    results = rag.similar_incidents("credential prompt", k=2)

    assert vector.last_search_k == 6
    assert [doc.metadata["number"] for doc in results] == ["INC1", "INC2"]

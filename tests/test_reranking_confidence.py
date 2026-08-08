"""Tests for evidence diversity and retrieval confidence policy."""

from langchain_core.documents import Document

from src.knowledge.confidence import EvidenceLevel, assess_confidence
from src.knowledge.reranking import rerank_documents


def test_reranker_deduplicates_content_and_limits_one_document_dominance():
    docs = [
        Document(
            page_content="Restart the VPN client.",
            metadata={"chunk_id": "a1", "document_id": "doc-a", "semantic_score": 0.90},
        ),
        Document(
            page_content="Restart the VPN client.",
            metadata={"chunk_id": "a2", "document_id": "doc-a", "semantic_score": 0.89},
        ),
        Document(
            page_content="Then validate MFA registration.",
            metadata={"chunk_id": "a3", "document_id": "doc-a", "semantic_score": 0.80},
        ),
        Document(
            page_content="Check the gateway health before escalation.",
            metadata={"chunk_id": "b1", "document_id": "doc-b", "semantic_score": 0.70},
        ),
    ]

    ranked = rerank_documents(docs, limit=4, max_per_document=2)

    assert len(ranked) == 3
    assert sum(doc.metadata["document_id"] == "doc-a" for doc in ranked) == 2
    assert any(doc.metadata["document_id"] == "doc-b" for doc in ranked)


def test_confidence_classifies_empty_weak_and_strong_evidence():
    empty = assess_confidence([], minimum=0.30, strong=0.55)
    weak = assess_confidence(
        [Document(page_content="x", metadata={"semantic_score": 0.40})],
        minimum=0.30,
        strong=0.55,
    )
    strong = assess_confidence(
        [Document(page_content="x", metadata={"semantic_score": 0.82})],
        minimum=0.30,
        strong=0.55,
    )

    assert empty.level is EvidenceLevel.INSUFFICIENT
    assert empty.should_answer is False
    assert weak.level is EvidenceLevel.WEAK
    assert weak.should_answer is True
    assert strong.level is EvidenceLevel.STRONG


def test_low_signal_evidence_abstains():
    decision = assess_confidence(
        [Document(page_content="x", metadata={"semantic_score": 0.12})],
        minimum=0.30,
        strong=0.55,
    )

    assert decision.level is EvidenceLevel.INSUFFICIENT
    assert decision.should_answer is False

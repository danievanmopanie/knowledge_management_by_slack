"""Tests for lexical fusion and deterministic IT query understanding."""

from langchain_core.documents import Document

from src.knowledge.lexical import lexical_search, reciprocal_rank_fusion
from src.knowledge.query_understanding import understand_query


def test_query_understanding_preserves_identifiers_and_quotes():
    hints = understand_query(
        'Please check INC0034291 after CHG0030551 on AIWUSMLES01 with "VPN timeout" error 0x80072'
    )

    assert hints.incident_ids == ("INC0034291",)
    assert hints.change_ids == ("CHG0030551",)
    assert "AIWUSMLES01" in hints.hostnames
    assert "0X80072" in hints.error_codes
    assert hints.quoted_phrases == ("VPN timeout",)
    assert "INC0034291" in hints.exact_terms
    assert hints.normalized.startswith("Please check")


def test_lexical_search_boosts_exact_identifier_match():
    docs = [
        Document(
            page_content="General VPN troubleshooting for remote users",
            metadata={"chunk_id": "general"},
        ),
        Document(
            page_content="Resolution details for INC0034291 affecting AIWUSMLES01",
            metadata={"chunk_id": "exact"},
        ),
    ]

    ranked = lexical_search(
        "what happened to INC0034291",
        docs,
        limit=2,
        exact_terms=("INC0034291",),
    )

    assert ranked[0].metadata["chunk_id"] == "exact"
    assert ranked[0].metadata["lexical_score"] > 0


def test_reciprocal_rank_fusion_combines_semantic_and_lexical_rankings():
    semantic = [
        Document(page_content="A", metadata={"chunk_id": "a", "semantic_score": 0.91}),
        Document(page_content="B", metadata={"chunk_id": "b", "semantic_score": 0.80}),
    ]
    lexical = [
        Document(page_content="B", metadata={"chunk_id": "b", "lexical_score": 4.2}),
        Document(page_content="C", metadata={"chunk_id": "c", "lexical_score": 3.1}),
    ]

    fused = reciprocal_rank_fusion(semantic, lexical, limit=3)

    assert fused[0].metadata["chunk_id"] == "b"
    assert {doc.metadata["chunk_id"] for doc in fused} == {"a", "b", "c"}

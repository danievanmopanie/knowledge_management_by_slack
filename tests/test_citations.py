"""Tests for governed evidence citation rendering."""

from src.knowledge.citations import evidence_labels, render_evidence_section, sanitize_citations
from src.knowledge.retrieval_models import RetrievalCandidate


def _candidate(evidence_id: str, source: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        evidence_id=evidence_id,
        content="text",
        metadata={"source": source, "version_id": f"v-{evidence_id}"},
        score=0.8,
    )


def test_evidence_labels_are_stable_and_traceable():
    candidates = [_candidate("chunk-1", "vpn.md"), _candidate("chunk-2", "mfa.md")]

    labels = evidence_labels(candidates)
    section = render_evidence_section(candidates)

    assert list(labels) == ["E1", "E2"]
    assert "[E1] vpn.md | chunk-1" in section
    assert "[E2] mfa.md | chunk-2" in section


def test_sanitize_citations_removes_fabricated_labels():
    answer = "Restart the client [E1]. Ignore the imaginary source [E99]."

    cleaned = sanitize_citations(answer, {"E1", "E2"})

    assert "[E1]" in cleaned
    assert "[E99]" not in cleaned

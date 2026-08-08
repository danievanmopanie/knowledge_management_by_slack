"""Retrieval confidence scoring and abstention policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from langchain_core.documents import Document


class EvidenceLevel(StrEnum):
    INSUFFICIENT = "insufficient"
    WEAK = "weak"
    STRONG = "strong"


@dataclass(frozen=True)
class ConfidenceDecision:
    score: float
    level: EvidenceLevel

    @property
    def should_answer(self) -> bool:
        return self.level is not EvidenceLevel.INSUFFICIENT


def _document_strength(document: Document) -> float:
    metadata = document.metadata or {}
    semantic = max(0.0, min(1.0, float(metadata.get("semantic_score") or 0.0)))
    lexical_raw = max(0.0, float(metadata.get("lexical_score") or 0.0))
    lexical = lexical_raw / (lexical_raw + 5.0) if lexical_raw else 0.0
    rerank = max(0.0, float(metadata.get("rerank_score") or 0.0))
    rerank_norm = rerank / (rerank + 1.0) if rerank else 0.0
    return max(semantic, lexical, rerank_norm)


def assess_confidence(
    documents: list[Document],
    *,
    minimum: float = 0.30,
    strong: float = 0.55,
) -> ConfidenceDecision:
    """Classify evidence strength using the best independent retrieval signal."""
    if not documents:
        return ConfidenceDecision(score=0.0, level=EvidenceLevel.INSUFFICIENT)
    score = max(_document_strength(document) for document in documents)
    if score < minimum:
        level = EvidenceLevel.INSUFFICIENT
    elif score < strong:
        level = EvidenceLevel.WEAK
    else:
        level = EvidenceLevel.STRONG
    return ConfidenceDecision(score=score, level=level)

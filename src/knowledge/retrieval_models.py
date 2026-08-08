"""Typed models for deterministic, access-controlled retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.documents import Document

from src.core.context import RequestContext


@dataclass(frozen=True)
class RetrievalQuery:
    """One governed retrieval request."""

    text: str
    context: RequestContext | None = None
    limit: int = 5
    graph_depth: int = 1
    where: dict[str, Any] | None = None


@dataclass(frozen=True)
class RetrievalCandidate:
    """A retrieval candidate with stable evidence identity and provenance."""

    evidence_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None

    @classmethod
    def from_document(cls, document: Document, rank: int) -> "RetrievalCandidate":
        metadata = dict(document.metadata or {})
        evidence_id = str(
            metadata.get("chunk_id")
            or metadata.get("id")
            or metadata.get("version_id")
            or metadata.get("source_id")
            or metadata.get("source")
            or f"candidate-{rank}"
        )
        score = metadata.get("score")
        return cls(
            evidence_id=evidence_id,
            content=document.page_content,
            metadata=metadata,
            score=float(score) if score is not None else None,
        )

    def to_document(self) -> Document:
        metadata = dict(self.metadata)
        metadata.setdefault("evidence_id", self.evidence_id)
        if self.score is not None:
            metadata["score"] = self.score
        return Document(page_content=self.content, metadata=metadata)


@dataclass
class RetrievalResult:
    """Access-filtered evidence returned by the retrieval pipeline."""

    candidates: list[RetrievalCandidate] = field(default_factory=list)
    graph_context: list[dict[str, Any]] = field(default_factory=list)
    query: str = ""

    @property
    def documents(self) -> list[Document]:
        """Compatibility view for callers still consuming LangChain documents."""
        return [candidate.to_document() for candidate in self.candidates]

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(candidate.evidence_id for candidate in self.candidates)

    def to_context_string(self, max_chars: int = 6000) -> str:
        parts: list[str] = []
        if self.candidates:
            parts.append("### Relevant Knowledge Articles & Notes")
            for i, candidate in enumerate(self.candidates, 1):
                source = candidate.metadata.get("source", "unknown")
                header = f"[{i}] Evidence: {candidate.evidence_id} | Source: {source}"
                if candidate.score is not None:
                    header += f" (relevance: {candidate.score:.2f})"
                parts.extend([header, candidate.content.strip(), ""])
        if self.graph_context:
            parts.append("### Related Entities & Relationships")
            for item in self.graph_context:
                parts.append(
                    f"- ({item.get('relation', 'related_to')}) {item.get('entity')} "
                    f"[{item.get('type', 'entity')}]"
                )
            parts.append("")
        text = "\n".join(parts).strip()
        return text if len(text) <= max_chars else text[:max_chars] + "\n\n[Context truncated]"

"""Evidence citation helpers for Slack RAG answers."""

from __future__ import annotations

import re

from src.knowledge.retrieval_models import RetrievalCandidate

_CITATION = re.compile(r"\[E(\d+)\]")


def evidence_labels(candidates: list[RetrievalCandidate]) -> dict[str, RetrievalCandidate]:
    return {f"E{index}": candidate for index, candidate in enumerate(candidates, 1)}


def sanitize_citations(answer: str, valid_labels: set[str]) -> str:
    """Remove model-invented evidence references that were not supplied."""
    def replace(match: re.Match[str]) -> str:
        label = f"E{match.group(1)}"
        return match.group(0) if label in valid_labels else ""

    return _CITATION.sub(replace, answer or "")


def render_evidence_section(candidates: list[RetrievalCandidate]) -> str:
    labels = evidence_labels(candidates)
    lines: list[str] = []
    for label, candidate in labels.items():
        source = str(candidate.metadata.get("source") or "unknown")
        version = str(candidate.metadata.get("version_id") or "")
        suffix = f" | version {version}" if version else ""
        lines.append(f"[{label}] {source} | {candidate.evidence_id}{suffix}")
    return "\n".join(lines)

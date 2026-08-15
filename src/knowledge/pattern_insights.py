"""Organisation-wide pattern expansion for enriched incident knowledge.

Semantic retrieval answers "which enriched cases look like this symptom?". This
module takes those candidate cases, identifies repeatedly represented canonical
issue patterns, then opens the full materialised rollup for those patterns.

This is deliberately deterministic: the response LLM receives exact counts from
SQLite and never estimates organisation-wide frequencies from a small top-k set.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.core.config import settings
from src.knowledge.organisational_knowledge import OrganisationalKnowledgeRetriever


def _render_rows(title: str, rows: list[dict[str, Any]], *, limit: int = 6) -> list[str]:
    if not rows:
        return []
    lines = [title]
    for row in rows[:limit]:
        incidents = ", ".join(row.get("incidents") or [])
        suffix = f" — examples: {incidents}" if incidents else ""
        lines.append(f"- {row.get('label')}: {int(row.get('count') or 0)} incident(s){suffix}")
    return lines


def organisation_wide_pattern_context(
    retriever: OrganisationalKnowledgeRetriever,
    query: str,
    *,
    candidate_k: int | None = None,
    min_similarity: float | None = None,
    min_candidate_support: int = 2,
    max_patterns: int = 2,
    max_chars: int = 9000,
) -> str:
    """Expand well-supported semantic candidates to full pattern rollups.

    A pattern is expanded only when at least ``min_candidate_support`` distinct
    semantically retrieved incidents map to it. This prevents one loosely similar
    incident from causing a broad organisation-wide claim.
    """
    query = " ".join((query or "").split())
    if not query:
        return ""
    threshold = settings.knowledge_min_similarity if min_similarity is None else float(min_similarity)
    k = candidate_k or settings.knowledge_pattern_candidate_k

    try:
        docs = retriever.index.vector_store.similarity_search(
            query,
            k=max(1, int(k)),
            where={"knowledge_kind": "symptom"},
        )
    except Exception:
        return ""

    best_scores: dict[str, float] = {}
    for doc in docs:
        meta = doc.metadata or {}
        number = str(meta.get("number") or "").strip().upper()
        score = float(meta.get("score") or 0.0)
        if not number or score < threshold:
            continue
        best_scores[number] = max(score, best_scores.get(number, -1.0))

    support: dict[str, set[str]] = defaultdict(set)
    score_sum: dict[str, float] = defaultdict(float)
    labels: dict[str, str] = {}
    for number, score in best_scores.items():
        item = retriever.store.get(number)
        if item is None or not item.pattern_key:
            continue
        support[item.pattern_key].add(number)
        score_sum[item.pattern_key] += score
        labels.setdefault(item.pattern_key, item.pattern_label or item.pattern_key)

    eligible = []
    for pattern_key, incidents in support.items():
        if len(incidents) < max(1, int(min_candidate_support)):
            continue
        avg_similarity = score_sum[pattern_key] / len(incidents)
        eligible.append((len(incidents), avg_similarity, pattern_key))
    eligible.sort(key=lambda item: (-item[0], -item[1], item[2]))

    sections: list[str] = []
    for candidate_support, avg_similarity, pattern_key in eligible[:max_patterns]:
        pattern = retriever.store.get_pattern(pattern_key)
        if not pattern:
            continue
        lines = [
            f"#### Organisation-wide pattern — {pattern.get('label') or labels.get(pattern_key) or pattern_key}",
            (
                f"Pattern selected because {candidate_support} semantically matching incident(s) in the "
                f"retrieved neighbourhood mapped to it (mean similarity {avg_similarity:.2f})."
            ),
            f"Total enriched incidents in this canonical pattern: {int(pattern.get('incident_count') or 0)}",
            f"Incidents with recorded resolution knowledge: {int(pattern.get('resolved_count') or 0)}",
            f"Average extraction confidence: {float(pattern.get('avg_confidence') or 0.0):.2f}",
        ]
        lines.extend(_render_rows("Organisation-wide recorded resolutions:", pattern.get("resolution_counts") or []))
        lines.extend(
            _render_rows(
                "Organisation-wide successful actions:",
                pattern.get("successful_action_counts") or [],
            )
        )
        lines.extend(
            _render_rows(
                "Organisation-wide failed/no-fix actions:",
                pattern.get("failed_action_counts") or [],
            )
        )
        lines.extend(_render_rows("Organisation-wide root causes:", pattern.get("root_cause_counts") or []))
        sections.append("\n".join(lines))

    if not sections:
        return ""
    text = (
        "### Organisation-wide reusable knowledge\n"
        "These are complete materialised rollups over every enriched incident assigned to the selected "
        "canonical issue pattern. Counts are SQLite facts, not LLM estimates.\n\n"
        + "\n\n".join(sections)
    )
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "\n[Pattern rollup truncated]"

"""Offline retrieval evaluation utilities for deterministic CI checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from src.core.context import RequestContext
from src.knowledge.lexical import lexical_search
from src.knowledge.query_understanding import understand_query
from src.security.access import can_read


@dataclass(frozen=True)
class EvaluationMetrics:
    recall_at_k: float
    mrr: float
    no_answer_accuracy: float
    cases: int


def _reciprocal_rank(retrieved_ids: list[str], expected_ids: set[str]) -> float:
    for rank, evidence_id in enumerate(retrieved_ids, 1):
        if evidence_id in expected_ids:
            return 1.0 / rank
    return 0.0


def evaluate_rankings(
    rankings: list[tuple[list[str], list[str], bool]],
    *,
    k: int = 5,
) -> EvaluationMetrics:
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    no_answer_results: list[float] = []

    for retrieved, expected, no_answer in rankings:
        retrieved_k = retrieved[:k]
        expected_set = set(expected)
        if expected_set:
            recalls.append(len(expected_set.intersection(retrieved_k)) / len(expected_set))
            reciprocal_ranks.append(_reciprocal_rank(retrieved_k, expected_set))
        if no_answer:
            no_answer_results.append(1.0 if not retrieved_k else 0.0)

    return EvaluationMetrics(
        recall_at_k=sum(recalls) / len(recalls) if recalls else 1.0,
        mrr=sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 1.0,
        no_answer_accuracy=(
            sum(no_answer_results) / len(no_answer_results) if no_answer_results else 1.0
        ),
        cases=len(rankings),
    )


def run_golden_dataset(path: Path, *, k: int = 5) -> EvaluationMetrics:
    """Run the deterministic lexical/ACL baseline from a version-controlled fixture."""
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    documents = [
        Document(
            page_content=item["text"],
            metadata={**item.get("metadata", {}), "chunk_id": item["id"]},
        )
        for item in payload.get("documents", [])
    ]

    rankings: list[tuple[list[str], list[str], bool]] = []
    for case in payload.get("cases", []):
        context = RequestContext.from_slack(
            channel_id="C-EVAL",
            user_id=case.get("user_id") or "U-EVAL",
        )
        allowed = [document for document in documents if can_read(document.metadata, context)]
        hints = understand_query(case["query"])
        ranked = lexical_search(
            hints.normalized,
            allowed,
            limit=k,
            exact_terms=hints.exact_terms,
        )
        retrieved_ids = [str(document.metadata.get("chunk_id")) for document in ranked]
        rankings.append(
            (
                retrieved_ids,
                list(case.get("expected_ids", [])),
                bool(case.get("no_answer", False)),
            )
        )

    return evaluate_rankings(rankings, k=k)

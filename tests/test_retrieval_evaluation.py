"""Regression check for the version-controlled retrieval golden dataset."""

from pathlib import Path

from src.knowledge.evaluation import run_golden_dataset


def test_golden_retrieval_baseline_meets_minimum_quality():
    metrics = run_golden_dataset(Path("tests/fixtures/retrieval_golden.json"), k=3)

    assert metrics.cases == 5
    assert metrics.recall_at_k >= 1.0
    assert metrics.mrr >= 1.0
    assert metrics.no_answer_accuracy >= 1.0

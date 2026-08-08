#!/usr/bin/env python3
"""Run the offline retrieval golden-set evaluation."""

from pathlib import Path

from src.knowledge.evaluation import run_golden_dataset


def main() -> None:
    metrics = run_golden_dataset(Path("tests/fixtures/retrieval_golden.json"), k=3)
    print(f"cases={metrics.cases}")
    print(f"recall@3={metrics.recall_at_k:.3f}")
    print(f"mrr={metrics.mrr:.3f}")
    print(f"no_answer_accuracy={metrics.no_answer_accuracy:.3f}")


if __name__ == "__main__":
    main()

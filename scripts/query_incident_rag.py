#!/usr/bin/env python3
"""Inspect field-aware historical incident retrieval without invoking the response LLM."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.incident_rag import IncidentRAG


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query the field-aware incident vector index and show why each incident matched."
    )
    parser.add_argument("query", help="Symptom, error, troubleshooting step, or known resolution")
    parser.add_argument("--limit", type=int, default=5, help="Maximum incidents to return")
    parser.add_argument(
        "--field",
        choices=("problem", "troubleshooting", "resolution"),
        help="Optionally restrict vector search to one incident field group",
    )
    args = parser.parse_args()

    where = {"field_group": args.field} if args.field else None
    docs = IncidentRAG().similar_incidents(args.query, k=max(1, args.limit), where=where)

    if not docs:
        print("No incident matches found.")
        return 0

    for rank, doc in enumerate(docs, 1):
        meta = doc.metadata or {}
        field_scores = {}
        try:
            field_scores = json.loads(str(meta.get("field_scores_json") or "{}"))
        except json.JSONDecodeError:
            pass

        print(
            f"{rank}. {meta.get('number', '?')} | relevance={float(meta.get('score', 0.0)):.3f} "
            f"| matched={meta.get('matched_fields', '')} | state={meta.get('state', '')}"
        )
        if field_scores:
            print(
                "   field scores: "
                + ", ".join(f"{field}={float(score):.3f}" for field, score in field_scores.items())
            )
        print(f"   group={meta.get('assignment_group', '')} | location={meta.get('location', '')}")
        preview = " ".join(doc.page_content.split())
        print(f"   {preview[:700]}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

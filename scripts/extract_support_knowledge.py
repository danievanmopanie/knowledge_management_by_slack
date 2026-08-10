"""Extract structured support knowledge from historical incident CSVs.

Examples:
    python scripts/extract_support_knowledge.py --limit 100
    python scripts/extract_support_knowledge.py --resolved-only
    python scripts/extract_support_knowledge.py --force --limit 20
"""

from __future__ import annotations

import argparse
import asyncio

from src.knowledge.support_extraction import SupportKnowledgeExtractor
from src.reporting.incidents import load_all_incidents


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Maximum incidents to extract; 0 means all")
    parser.add_argument("--force", action="store_true", help="Ignore the content-hash extraction cache")
    parser.add_argument(
        "--resolved-only",
        action="store_true",
        help="Only process Resolved/Closed incidents",
    )
    parser.add_argument("--concurrency", type=int, default=None)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    incidents = load_all_incidents()
    if args.resolved_only:
        incidents = [
            incident
            for incident in incidents
            if (incident.state or "").strip().lower() in {"resolved", "closed"}
        ]
    if args.limit > 0:
        incidents = incidents[: args.limit]

    extractor = SupportKnowledgeExtractor()
    stats = await extractor.extract_many(
        incidents,
        force=args.force,
        concurrency=args.concurrency,
    )
    print(
        f"Support extraction complete: processed={stats['processed']} "
        f"failed={stats['failed']} total_selected={len(incidents)}"
    )


if __name__ == "__main__":
    asyncio.run(main())

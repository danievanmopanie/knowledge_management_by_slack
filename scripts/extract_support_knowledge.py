"""Extract structured support knowledge from historical incident CSVs.

Examples:
    python scripts/extract_support_knowledge.py --limit 100
    python scripts/extract_support_knowledge.py --resolved-only
    python scripts/extract_support_knowledge.py --force --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from src.core.config import settings
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    args = parse_args()
    incidents = load_all_incidents()
    loaded_count = len(incidents)

    if args.resolved_only:
        incidents = [
            incident
            for incident in incidents
            if (incident.state or "").strip().lower() in {"resolved", "closed"}
        ]
    eligible_count = len(incidents)

    if args.limit > 0:
        incidents = incidents[: args.limit]

    worker_count = args.concurrency or settings.support_extraction_concurrency
    print(
        "Support extraction starting\n"
        f"  loaded_from_disk: {loaded_count}\n"
        f"  eligible_after_filters: {eligible_count}\n"
        f"  selected_this_run: {len(incidents)}\n"
        f"  model: {settings.support_extraction_model}\n"
        f"  concurrency: {worker_count}\n"
        f"  force: {args.force}\n",
        flush=True,
    )

    if not incidents:
        print("No incidents matched the requested filters.", flush=True)
        return

    extractor = SupportKnowledgeExtractor()
    stats = await extractor.extract_many(
        incidents,
        force=args.force,
        concurrency=args.concurrency,
    )
    print(
        "Support extraction complete: "
        f"processed={stats['processed']} "
        f"cached={stats['cached']} "
        f"extracted={stats['extracted']} "
        f"failed={stats['failed']} "
        f"total_selected={len(incidents)}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())

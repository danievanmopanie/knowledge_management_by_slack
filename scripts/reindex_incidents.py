#!/usr/bin/env python3
"""Reindex incident CSVs into incident RAG (with content-hash dedupe)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.incident_rag import IncidentRAG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("reindex_incidents")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reindex ServiceNow incident CSVs")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-embed all rows even if content hash is unchanged",
    )
    args = parser.parse_args()

    try:
        result = IncidentRAG().index_from_disk(force=args.force)
        logger.info("Reindex complete: %s", result)
        print(result)
        return 0
    except Exception:
        logger.exception("Incident reindex failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run a scheduled knowledge-base backup (for cron / systemd)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.backup import run_scheduled_backup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("scheduled_backup")


def main() -> int:
    try:
        result = run_scheduled_backup()
        logger.info("Scheduled backup created: %s", result["backup"])
        if result["pruned"]:
            logger.info("Pruned %d old backup(s): %s", len(result["pruned"]), ", ".join(result["pruned"]))
        else:
            logger.info("No backups pruned (retention=%s)", result["retention"])
        print(result["backup"])
        return 0
    except Exception:
        logger.exception("Scheduled backup failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate and publish the afternoon Frontend Support handover."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import settings
from src.reporting.afternoon import generate_afternoon_handover_report
from src.reporting.publisher import publish_report_to_channel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("afternoon_report")


async def _run() -> None:
    if not settings.report_afternoon_enabled:
        logger.info("Afternoon report disabled via REPORT_AFTERNOON_ENABLED=false")
        return

    report = await generate_afternoon_handover_report(
        hours=settings.report_afternoon_hours
    )
    result = publish_report_to_channel(report)
    logger.info("Afternoon report published: %s", result)


def main() -> int:
    try:
        asyncio.run(_run())
        return 0
    except Exception:
        logger.exception("Afternoon report job failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

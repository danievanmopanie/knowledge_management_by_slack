#!/usr/bin/env python3
"""Generate and publish the weekly performance report to #frontend-support."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import settings
from src.reporting.publisher import publish_report_to_channel
from src.reporting.weekly import generate_weekly_performance_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("weekly_report")


async def _run() -> None:
    if not settings.report_weekly_enabled:
        logger.info("Weekly report disabled via REPORT_WEEKLY_ENABLED=false")
        return

    location = settings.report_weekly_location
    if not location:
        logger.warning(
            "REPORT_WEEKLY_LOCATION is not set – weekly report will include all locations"
        )

    report = await generate_weekly_performance_report(location=location)
    result = publish_report_to_channel(report)
    logger.info("Weekly report published: %s", result)


def main() -> int:
    try:
        asyncio.run(_run())
        return 0
    except Exception:
        logger.exception("Weekly report job failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

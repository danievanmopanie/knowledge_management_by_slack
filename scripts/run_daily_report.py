#!/usr/bin/env python3
"""Generate and publish the daily Focus of the Day report to #frontend-support."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import settings
from src.reporting.daily import generate_daily_focus_report
from src.reporting.publisher import publish_report_to_channel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("daily_report")


async def _run() -> None:
    if not settings.report_daily_enabled:
        logger.info("Daily report disabled via REPORT_DAILY_ENABLED=false")
        return

    report = await generate_daily_focus_report(hours=settings.report_daily_hours)
    result = publish_report_to_channel(report)
    logger.info("Daily report published: %s", result)


def main() -> int:
    try:
        asyncio.run(_run())
        return 0
    except Exception:
        logger.exception("Daily report job failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

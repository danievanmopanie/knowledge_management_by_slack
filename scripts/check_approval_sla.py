#!/usr/bin/env python3
"""Routine Keeper check: has every submitted request been decided within SLA? (weekday mornings)"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import settings
from src.work_management.routine_keeper import check_approval_sla

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("wm_approval_sla_check")


def main() -> int:
    if not settings.routine_keeper_enabled:
        logger.info("Routine Keeper disabled via ROUTINE_KEEPER_ENABLED=false")
        return 0
    try:
        result = check_approval_sla()
        logger.info("Approval SLA check result: %s", result)
        return 0
    except Exception:
        logger.exception("Approval SLA check failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

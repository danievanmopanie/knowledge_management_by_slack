#!/usr/bin/env python3
"""Run the Builder Agent background worker (long-running poll loop, for systemd)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.worker.builder_worker import run_forever

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("builder_worker")


def main() -> int:
    try:
        run_forever()
        return 0
    except KeyboardInterrupt:
        logger.info("Builder worker stopped by signal")
        return 0
    except Exception:
        logger.exception("Builder worker crashed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

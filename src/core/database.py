"""Shared SQLite connection for platform metadata and audit state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.core.config import settings


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path or settings.platform_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

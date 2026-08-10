"""Subprocess wrapper for invoking Aider against an isolated worktree."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AiderResult:
    success: bool
    stdout: str
    stderr: str
    returncode: int


def run_aider(*, goal: str, worktree_path: Path) -> AiderResult:
    """Run Aider non-interactively inside worktree_path, auto-committing its changes."""
    cmd = [
        "aider",
        "--model",
        settings.builder_aider_model,
        "--yes-always",
        "--no-gitignore",
        "--auto-commits",
        "--message",
        goal,
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=settings.builder_task_timeout_seconds,
        )
        return AiderResult(
            success=proc.returncode == 0,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning("Aider run timed out for %s", worktree_path)
        return AiderResult(
            success=False,
            stdout=(exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr="Aider run timed out.",
            returncode=-1,
        )

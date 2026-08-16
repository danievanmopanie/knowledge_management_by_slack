"""Subprocess wrapper for invoking Aider against an isolated worktree."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.core.config import settings
from src.worker.validation import resolved_test_command

logger = logging.getLogger(__name__)


@dataclass
class AiderResult:
    success: bool
    stdout: str
    stderr: str
    returncode: int


def _aider_environment() -> dict[str, str]:
    """Return an environment that points Aider at the Builder-only LLM API."""
    env = os.environ.copy()
    if settings.builder_llm_base_url:
        base_url = settings.builder_llm_base_url.rstrip("/")
        # Aider/LiteLLM accepts the standard OpenAI variables. The AIDER_
        # aliases keep the endpoint explicit for newer Aider releases too.
        env["OPENAI_API_BASE"] = base_url
        env["AIDER_OPENAI_API_BASE"] = base_url
    if settings.builder_llm_api_key:
        env["OPENAI_API_KEY"] = settings.builder_llm_api_key
        env["AIDER_OPENAI_API_KEY"] = settings.builder_llm_api_key
    return env


def run_aider(*, goal: str, worktree_path: Path) -> AiderResult:
    """Run Aider non-interactively and let it exercise the local test suite.

    Aider's auto-test loop is the first repair layer. The Builder worker runs a
    second, independent validation command before it is allowed to push a
    branch or create a pull request.
    """
    cmd = [
        "aider",
        "--model",
        settings.builder_aider_model,
        "--yes-always",
        "--no-gitignore",
        "--auto-commits",
        "--no-check-update",
    ]
    test_command = resolved_test_command()
    if test_command:
        cmd.extend(["--test-cmd", test_command, "--auto-test"])
    cmd.extend(["--message", goal])

    try:
        proc = subprocess.run(
            cmd,
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=settings.builder_task_timeout_seconds,
            env=_aider_environment(),
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

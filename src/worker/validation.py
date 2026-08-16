"""Local validation gate for Builder Agent worktrees."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    success: bool
    command: str
    stdout: str
    stderr: str
    returncode: int

    @property
    def output(self) -> str:
        text = "\n".join(part for part in (self.stdout, self.stderr) if part).strip()
        if not text:
            return "(validation produced no output)"
        return text[-settings.builder_validation_output_chars :]


def resolved_test_command() -> str:
    """Resolve the configured test command for the worker's virtualenv."""
    python = shlex.quote(sys.executable)
    return settings.builder_test_command.replace("{python}", python).strip()


def run_validation(*, worktree_path: Path) -> ValidationResult:
    """Execute the configured validation command inside an isolated worktree.

    The command comes from deployment configuration, never from a Slack
    message. This deliberately executes repository code because the Builder
    Agent is intended to prove its changes on the GX10 before publishing them.
    """
    command = resolved_test_command()
    if not command:
        if settings.builder_require_tests_pass:
            return ValidationResult(
                success=False,
                command="",
                stdout="",
                stderr="BUILDER_TEST_COMMAND is empty while tests are required.",
                returncode=-2,
            )
        return ValidationResult(success=True, command="", stdout="", stderr="", returncode=0)

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    logger.info("Builder validation cmd: %s (cwd=%s)", command, worktree_path)
    try:
        proc = subprocess.run(
            ["/bin/bash", "-lc", command],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=settings.builder_test_timeout_seconds,
            env=env,
        )
        return ValidationResult(
            success=proc.returncode == 0,
            command=command,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return ValidationResult(
            success=False,
            command=command,
            stdout=stdout,
            stderr=(stderr + "\nBuilder validation timed out.").strip(),
            returncode=-1,
        )

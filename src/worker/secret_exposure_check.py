"""Startup self-check for accidentally-readable deployment secrets.

The terminal harness's shell-command blocklist (see `terminal_harness.py`) is a
string-match deterrent, not a security boundary — it can be defeated by
indirection (`python -c "..."`, base64, string concatenation). The real
boundary is whether the OS account running `builder-worker.service` can itself
read secret material off disk: if this process can, any `run_shell` subprocess
it spawns can too, regardless of what the blocklist matches.

This module does not enforce anything. It only detects a misconfigured
deployment and surfaces it loudly (log + a one-time Slack message) instead of
silently trusting that file permissions were set correctly.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)

_SSH_KEY_NAMES = ("id_rsa", "id_ed25519", "id_ecdsa")


@dataclass
class SecretExposureFinding:
    path: str
    reason: str


def _readable(path: Path) -> bool:
    """Return True only if the path exists AND a real read succeeds.

    `os.access` alone can be fooled by ACLs/capabilities in edge cases; an
    actual bounded read attempt is the more honest signal for "can this
    process's child processes read this file too."
    """
    try:
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            handle.read(1)
        return True
    except OSError:
        return False


def _candidate_paths() -> list[Path]:
    candidates: list[Path] = [
        Path(settings.builder_repo_path) / ".env",
        Path.cwd() / ".env",
    ]
    home = Path.home()
    for name in _SSH_KEY_NAMES:
        candidates.append(home / ".ssh" / name)
    extra = settings.builder_secret_check_extra_paths
    if extra:
        candidates.extend(Path(item.strip()) for item in extra.split(",") if item.strip())
    return candidates


def check_secret_exposure() -> list[SecretExposureFinding]:
    """Probe whether this process can itself read deployment secrets."""
    findings: list[SecretExposureFinding] = []
    seen: set[Path] = set()
    for path in _candidate_paths():
        resolved = path if path.is_absolute() else path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if _readable(resolved):
            findings.append(
                SecretExposureFinding(
                    path=str(resolved),
                    reason=(
                        "readable by the account running builder-worker.service — any "
                        "run_shell command it executes can read it too, regardless of the "
                        "shell command blocklist"
                    ),
                )
            )
    return findings


def log_and_warn_findings(findings: list[SecretExposureFinding]) -> None:
    if not findings:
        return
    for finding in findings:
        logger.critical("Builder secret exposure: %s — %s", finding.path, finding.reason)

    allowed_ids = [
        item.strip()
        for item in settings.builder_agent_allowed_user_ids.split(",")
        if item.strip()
    ]
    if not allowed_ids or not settings.channel_builder_agent:
        return

    from src.reporting.publisher import publish_report_to_channel

    mentions = " ".join(f"<@{user_id}>" for user_id in allowed_ids)
    lines = "\n".join(f"• `{finding.path}` — {finding.reason}" for finding in findings)
    try:
        publish_report_to_channel(
            f"{mentions} ⚠️ Builder secret-exposure check failed on startup:\n{lines}\n\n"
            "Lock down file permissions (see `scripts/harden_builder_secrets.sh` and "
            "`docs/BUILDER_ATLAS_AEON.md`) before trusting the terminal harness's secret "
            "protections.",
            channel_id=settings.channel_builder_agent,
        )
    except Exception:
        logger.exception("Failed to publish Builder secret-exposure warning to Slack")

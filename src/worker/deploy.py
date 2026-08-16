"""Deterministic merge-and-deploy path for the Slack "Merge & Deploy" button.

This module is never imported by `src/worker/terminal_harness.py` and must
stay that way. Merging a pull request and restarting a systemd unit are fixed,
human-triggered operations reached only through an explicit, allowlist-checked
Slack button click (see `src/bot/builder_interactivity.py`) — never something
the AEON tool loop can decide to do on its own. The (deliberately no-sudo)
terminal harness and this module are separate trust boundaries: this module's
narrow `sudo systemctl restart <unit>` privilege does not extend to it.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field

from src.core.config import settings
from src.integrations.github_client import (
    GitHubClientError,
    get_pull_request_state,
    merge_pull_request,
)

logger = logging.getLogger(__name__)


@dataclass
class DeployResult:
    success: bool
    merged: bool
    already_merged: bool
    merge_sha: str | None
    restarted: list[tuple[str, bool, str]] = field(default_factory=list)
    message: str = ""


def _configured_units() -> list[str]:
    return [item.strip() for item in settings.builder_deploy_restart_units.split(",") if item.strip()]


def _is_active(unit: str) -> tuple[bool, str]:
    """Unprivileged health check — no sudo required for `systemctl is-active`."""
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", unit],
            timeout=settings.builder_deploy_health_check_timeout_seconds,
            capture_output=True,
            text=True,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"health check failed: {exc}"
    state = (proc.stdout or "").strip() or "unknown"
    return state == "active", state


def _restart_unit(unit: str) -> tuple[bool, str]:
    """Restart one systemd unit via the narrowly-scoped sudoers rule.

    See deploy/sudoers/builder-deploy — this must be an exact-argument
    NOPASSWD entry for `systemctl restart <unit>`, not a wildcard.
    """
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", unit],
            timeout=settings.builder_deploy_restart_timeout_seconds,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return False, (exc.stderr or exc.stdout or "restart command failed")[:500]
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"restart failed: {exc}"
    return _is_active(unit)


def merge_and_deploy(*, pr_number: str, pr_url: str) -> DeployResult:
    """Merge a green PR (idempotently) and restart the configured GX10 unit(s).

    Re-checks the PR's current head SHA immediately before merging so a branch
    that moved since the Slack card was last rendered fails safely (the
    GitHub API returns 409) instead of merging commits nobody validated. If
    the PR is already merged — a repeat click, or someone merged it manually
    in the meantime — this skips straight to the restart step rather than
    erroring, so retrying after a partial failure is safe.
    """
    units = _configured_units()
    if not units:
        return DeployResult(
            success=False,
            merged=False,
            already_merged=False,
            merge_sha=None,
            message="No BUILDER_DEPLOY_RESTART_UNITS configured; nothing to restart.",
        )

    try:
        state = get_pull_request_state(pr_number)
    except GitHubClientError as exc:
        return DeployResult(
            success=False,
            merged=False,
            already_merged=False,
            merge_sha=None,
            message=f"Could not resolve PR #{pr_number}: {exc}",
        )

    already_merged = state["merged"]
    merge_sha: str | None = state["head_sha"] or None

    if not already_merged:
        if state["state"] != "open":
            return DeployResult(
                success=False,
                merged=False,
                already_merged=False,
                merge_sha=None,
                message=f"PR #{pr_number} is closed and was never merged; nothing to deploy.",
            )
        try:
            merge_result = merge_pull_request(pr_number, sha=state["head_sha"])
        except GitHubClientError as exc:
            return DeployResult(
                success=False,
                merged=False,
                already_merged=False,
                merge_sha=None,
                message=f"Merge failed: {exc}",
            )
        merge_sha = merge_result.get("sha") or merge_sha

    restarted: list[tuple[str, bool, str]] = []
    all_active = True
    for unit in units:
        is_active, detail = _restart_unit(unit)
        restarted.append((unit, is_active, detail))
        if not is_active:
            all_active = False

    if all_active:
        message = ("Already merged; " if already_merged else "Merged; ") + "all configured unit(s) restarted and active."
    else:
        message = (
            ("Already merged; " if already_merged else "Merged; ")
            + "one or more units failed to restart or become active — check the GX10 service logs."
        )

    logger.info(
        "Builder merge_and_deploy pr=%s already_merged=%s restarted=%s",
        pr_number,
        already_merged,
        restarted,
    )
    return DeployResult(
        success=all_active,
        merged=not already_merged,
        already_merged=already_merged,
        merge_sha=merge_sha,
        restarted=restarted,
        message=message,
    )

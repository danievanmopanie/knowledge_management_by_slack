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
from pathlib import Path

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
    # Set when one of the configured units is this process's own unit
    # (BUILDER_DEPLOY_SELF_UNIT). The caller must finalize the deployment
    # record and Slack card BEFORE triggering this restart — see
    # trigger_pending_self_restart() — because issuing it will kill this
    # process before any code after that point can run.
    pending_self_restart: str | None = None


def _configured_units() -> list[str]:
    return [item.strip() for item in settings.builder_deploy_restart_units.split(",") if item.strip()]


def _checkout_path() -> Path:
    configured = settings.builder_deploy_checkout_path.strip()
    return Path(configured) if configured else Path.cwd()


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        timeout=settings.builder_deploy_restart_timeout_seconds,
        capture_output=True,
        text=True,
        check=True,
    )


def _sync_checkout(checkout_path: Path, target_sha: str) -> tuple[bool, str]:
    """Fetch and hard-reset the live checkout to the exact merged commit.

    Restarting a systemd unit only re-execs whatever is already on disk at
    its WorkingDirectory — it does not pull anything. Without this step,
    Merge & Deploy would restart services into the OLD code while reporting
    the change as deployed. Fails closed (no reset) if the checkout is dirty
    or has local commits that are not an ancestor of the merged commit,
    rather than silently discarding work that might matter.
    """
    if not (checkout_path / ".git").exists():
        return False, f"{checkout_path} is not a git checkout; refusing to sync"

    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=checkout_path,
            timeout=settings.builder_deploy_restart_timeout_seconds,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"could not inspect live checkout status at {checkout_path}: {exc}"
    if dirty:
        return False, f"live checkout at {checkout_path} has uncommitted changes; refusing to overwrite them"

    try:
        _run_git(["fetch", settings.builder_git_remote, settings.builder_base_branch], cwd=checkout_path)
        head = _run_git(["rev-parse", "HEAD"], cwd=checkout_path).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"could not fetch/inspect live checkout at {checkout_path}: {exc}"

    if head == target_sha:
        return True, f"live checkout already at {target_sha[:12]}"

    is_ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", head, target_sha],
        cwd=checkout_path,
        timeout=settings.builder_deploy_restart_timeout_seconds,
        capture_output=True,
        text=True,
        check=False,
    )
    if is_ancestor.returncode != 0:
        return False, (
            f"live checkout HEAD ({head[:12]}) is not an ancestor of the merged commit "
            f"({target_sha[:12]}); it has diverged and will not be overwritten automatically"
        )

    try:
        _run_git(["reset", "--hard", target_sha], cwd=checkout_path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return False, f"failed to sync live checkout to {target_sha[:12]}: {exc}"
    return True, f"live checkout synced {head[:12]} -> {target_sha[:12]}"


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


def trigger_pending_self_restart(unit: str) -> None:
    """Fire-and-forget restart of the unit this process runs under.

    Must only be called AFTER the deployment record and Slack card have been
    finalized — `sudo systemctl restart <this process's own unit>` kills this
    process, so nothing after this call is guaranteed to run. Uses Popen
    (non-blocking) rather than run(): a blocking call here would hang until
    the parent is killed and never return control to the caller anyway.
    """
    try:
        subprocess.Popen(
            ["sudo", "systemctl", "restart", unit],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        logger.exception("Failed to issue self-restart for unit %s", unit)


def merge_and_deploy(*, pr_number: str, pr_url: str) -> DeployResult:
    """Merge a green PR (idempotently), sync the live checkout, and restart
    the configured GX10 unit(s).

    Re-checks the PR's current head SHA immediately before merging so a branch
    that moved since the Slack card was last rendered fails safely (the
    GitHub API returns 409) instead of merging commits nobody validated. If
    the PR is already merged — a repeat click, or someone merged it manually
    in the meantime — this skips straight to the sync/restart steps rather
    than erroring, so retrying after a partial failure is safe.

    If BUILDER_DEPLOY_SELF_UNIT names one of the configured restart units,
    that unit's restart is deferred: the caller must finalize the deployment
    record and Slack card using the returned result, THEN call
    trigger_pending_self_restart() as the very last action, since restarting
    this process's own unit kills it before any later code can run.
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

    if not merge_sha:
        return DeployResult(
            success=False,
            merged=not already_merged,
            already_merged=already_merged,
            merge_sha=None,
            message="No merge commit SHA available; refusing to sync/restart against unknown code.",
        )

    sync_ok, sync_detail = _sync_checkout(_checkout_path(), merge_sha)
    if not sync_ok:
        return DeployResult(
            success=False,
            merged=not already_merged,
            already_merged=already_merged,
            merge_sha=merge_sha,
            message=(("Already merged; " if already_merged else "Merged; ") + f"not restarted: {sync_detail}"),
        )

    self_unit = settings.builder_deploy_self_unit.strip()
    pending_self_restart = self_unit if self_unit in units else None
    restart_now = [unit for unit in units if unit != pending_self_restart]

    restarted: list[tuple[str, bool, str]] = []
    all_active = True
    for unit in restart_now:
        is_active, detail = _restart_unit(unit)
        restarted.append((unit, is_active, detail))
        if not is_active:
            all_active = False

    prefix = "Already merged; " if already_merged else "Merged; "
    if pending_self_restart:
        detail = "; some units failed to restart or become active" if not all_active else ""
        message = (
            f"{prefix}{sync_detail}; {len(restart_now)} unit(s) restarted{detail}. "
            f"Restarting {pending_self_restart} now (cannot be health-checked from within "
            "the process being restarted)."
        )
    elif all_active:
        message = f"{prefix}{sync_detail}; all configured unit(s) restarted and active."
    else:
        message = f"{prefix}{sync_detail}; one or more units failed to restart or become active — check the GX10 service logs."

    logger.info(
        "Builder merge_and_deploy pr=%s already_merged=%s restarted=%s pending_self_restart=%s",
        pr_number,
        already_merged,
        restarted,
        pending_self_restart,
    )
    return DeployResult(
        success=all_active,
        merged=not already_merged,
        already_merged=already_merged,
        merge_sha=merge_sha,
        restarted=restarted,
        message=message,
        pending_self_restart=pending_self_restart,
    )

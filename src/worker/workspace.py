"""Isolated git worktree lifecycle for Builder Agent turns."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)


class WorkspaceError(Exception):
    """Raised when preparing or tearing down a task worktree fails."""


@dataclass
class Worktree:
    task_id: str
    branch_name: str
    path: Path
    start_sha: str | None = None


def prepare_worktree(task_id: str, *, continuation_branch: str | None = None) -> Worktree:
    """Create an isolated worktree, optionally continuing an existing PR branch."""
    repo_path = Path(settings.builder_repo_path)
    branch_name = continuation_branch or f"builder/{task_id}"
    worktree_path = Path(settings.builder_workdir) / task_id
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if continuation_branch:
            _run(["git", "fetch", settings.builder_git_remote, continuation_branch], cwd=repo_path)
            # -B resets/creates the local branch at the remote PR head. The
            # previous task's worktree has already been removed, so the branch
            # is not checked out elsewhere.
            _run(
                [
                    "git",
                    "worktree",
                    "add",
                    "-B",
                    branch_name,
                    str(worktree_path),
                    f"{settings.builder_git_remote}/{continuation_branch}",
                ],
                cwd=repo_path,
            )
        else:
            _run(["git", "fetch", settings.builder_git_remote, settings.builder_base_branch], cwd=repo_path)
            _run(
                [
                    "git",
                    "worktree",
                    "add",
                    "-b",
                    branch_name,
                    str(worktree_path),
                    f"{settings.builder_git_remote}/{settings.builder_base_branch}",
                ],
                cwd=repo_path,
            )
        start_sha = _run(["git", "rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise WorkspaceError(f"Failed to prepare worktree for {task_id}: {exc.stderr}") from exc

    return Worktree(
        task_id=task_id,
        branch_name=branch_name,
        path=worktree_path,
        start_sha=start_sha,
    )


def prune_orphaned_worktrees() -> None:
    """Clear git's worktree registrations left behind by a crashed worker.

    Safe to call on every startup, whether or not anything is actually
    orphaned — `git worktree prune` is a no-op when there's nothing stale.
    """
    repo_path = Path(settings.builder_repo_path)
    try:
        _run(["git", "worktree", "prune"], cwd=repo_path)
    except subprocess.CalledProcessError:
        logger.exception("git worktree prune failed for %s", repo_path)


def cleanup_worktree(worktree: Worktree) -> None:
    """Remove the worktree directory and its git registration. Safe to call twice."""
    repo_path = Path(settings.builder_repo_path)
    try:
        _run(["git", "worktree", "remove", "--force", str(worktree.path)], cwd=repo_path)
    except subprocess.CalledProcessError:
        logger.warning("git worktree remove failed for %s, forcing prune", worktree.path)
        try:
            _run(["git", "worktree", "prune"], cwd=repo_path)
        except subprocess.CalledProcessError:
            logger.exception("git worktree prune also failed for %s", worktree.path)


def has_repository_changes(worktree: Worktree) -> bool:
    """Return True when this turn changed the worktree relative to its start SHA."""
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree.path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        return True
    if not worktree.start_sha:
        return False
    commits = subprocess.run(
        ["git", "rev-list", "--count", f"{worktree.start_sha}..HEAD"],
        cwd=worktree.path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return int(commits or "0") > 0


def commit_pending_changes(worktree: Worktree, *, message: str) -> None:
    """Commit any edits left uncommitted by the coding harness."""
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree.path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not dirty:
        return
    _run(["git", "add", "-A"], cwd=worktree.path)
    _run(["git", "commit", "-m", message], cwd=worktree.path)


def push_branch(worktree: Worktree) -> None:
    try:
        _run(["git", "push", settings.builder_git_remote, worktree.branch_name], cwd=worktree.path)
    except subprocess.CalledProcessError as exc:
        raise WorkspaceError(f"Failed to push branch {worktree.branch_name}: {exc.stderr}") from exc


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    logger.info("worker cmd: %s (cwd=%s)", " ".join(cmd), cwd)
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)

"""Tests for Builder Agent worktree lifecycle against a real throwaway git repo."""

import subprocess
from pathlib import Path

import pytest

from src.core.config import settings
from src.worker.workspace import prepare_worktree, cleanup_worktree, push_branch


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial"], check=True, capture_output=True)


@pytest.fixture
def builder_repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)

    seed = tmp_path / "seed"
    _init_repo(seed)
    subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "origin", "main"], check=True, capture_output=True)

    repo_path = tmp_path / "worker_repo"
    subprocess.run(["git", "clone", str(origin), str(repo_path)], check=True, capture_output=True)

    monkeypatch.setattr(settings, "builder_repo_path", repo_path)
    monkeypatch.setattr(settings, "builder_workdir", tmp_path / "worktrees")
    monkeypatch.setattr(settings, "builder_git_remote", "origin")
    monkeypatch.setattr(settings, "builder_base_branch", "main")
    return repo_path


def test_prepare_worktree_creates_branch_and_checkout(builder_repo):
    worktree = prepare_worktree("bld_test1")

    assert worktree.path.exists()
    assert (worktree.path / "README.md").exists()
    assert worktree.branch_name == "builder/bld_test1"

    branches = subprocess.run(
        ["git", "-C", str(builder_repo), "branch", "--list", worktree.branch_name],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert worktree.branch_name in branches

    cleanup_worktree(worktree)
    assert not worktree.path.exists()


def test_cleanup_worktree_is_safe_to_call_twice(builder_repo):
    worktree = prepare_worktree("bld_test2")

    cleanup_worktree(worktree)
    cleanup_worktree(worktree)  # should not raise

    assert not worktree.path.exists()


def test_push_branch_pushes_commit_to_remote(builder_repo):
    worktree = prepare_worktree("bld_test3")
    (worktree.path / "new_file.txt").write_text("content\n")
    subprocess.run(["git", "-C", str(worktree.path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(worktree.path), "commit", "-m", "add file"],
        check=True,
        capture_output=True,
    )

    push_branch(worktree)

    ls_remote = subprocess.run(
        ["git", "-C", str(builder_repo), "ls-remote", "origin", worktree.branch_name],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert worktree.branch_name in ls_remote

    cleanup_worktree(worktree)

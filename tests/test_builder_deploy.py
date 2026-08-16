"""Tests for the deterministic Merge & Deploy path (src/worker/deploy.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.core.config import settings
from src.integrations.github_client import GitHubClientError
from src.worker.deploy import _sync_checkout, merge_and_deploy, trigger_pending_self_restart


def _state(*, merged: bool, state: str = "open", head_sha: str = "abc123"):
    return {"number": "83", "state": state, "merged": merged, "head_sha": head_sha}


def _stub_sync(monkeypatch, *, ok: bool = True, detail: str = "live checkout synced abc123"):
    monkeypatch.setattr("src.worker.deploy._sync_checkout", lambda checkout_path, target_sha: (ok, detail))


def _fake_subprocess_run(monkeypatch, *, restart_ok: bool = True, active_state: str = "active"):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "sudo":
            if not restart_ok:
                raise subprocess.CalledProcessError(1, cmd, output="", stderr="restart failed")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "systemctl":
            return subprocess.CompletedProcess(cmd, 0, stdout=active_state, stderr="")
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr("src.worker.deploy.subprocess.run", fake_run)
    return calls


def test_merge_and_deploy_success(monkeypatch):
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "knowledge-management-by-slack.service")
    monkeypatch.setattr(settings, "builder_deploy_self_unit", "")
    _stub_sync(monkeypatch)
    calls = _fake_subprocess_run(monkeypatch)
    monkeypatch.setattr("src.worker.deploy.get_pull_request_state", lambda pr_number: _state(merged=False))
    merge_calls = []
    monkeypatch.setattr(
        "src.worker.deploy.merge_pull_request",
        lambda pr_number, *, sha, merge_method=None: merge_calls.append((pr_number, sha)) or {
            "merged": True,
            "sha": "def456",
            "message": "Merged",
        },
    )

    result = merge_and_deploy(pr_number="83", pr_url="https://github.com/org/repo/pull/83")

    assert result.success is True
    assert result.merged is True
    assert result.already_merged is False
    assert result.merge_sha == "def456"
    assert result.pending_self_restart is None
    assert merge_calls == [("83", "abc123")]
    assert result.restarted == [("knowledge-management-by-slack.service", True, "active")]
    assert any(call[0] == "sudo" for call in calls)


def test_merge_and_deploy_is_idempotent_when_already_merged(monkeypatch):
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "svc.service")
    monkeypatch.setattr(settings, "builder_deploy_self_unit", "")
    _stub_sync(monkeypatch)
    _fake_subprocess_run(monkeypatch)
    monkeypatch.setattr(
        "src.worker.deploy.get_pull_request_state", lambda pr_number: _state(merged=True, head_sha="xyz")
    )
    monkeypatch.setattr(
        "src.worker.deploy.merge_pull_request",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not merge an already-merged PR")),
    )

    result = merge_and_deploy(pr_number="83", pr_url="https://github.com/org/repo/pull/83")

    assert result.success is True
    assert result.merged is False
    assert result.already_merged is True
    assert result.merge_sha == "xyz"


def test_merge_and_deploy_merge_failure_skips_sync_and_restart(monkeypatch):
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "svc.service")
    sync_calls = []
    monkeypatch.setattr(
        "src.worker.deploy._sync_checkout",
        lambda checkout_path, target_sha: sync_calls.append(target_sha) or (True, "synced"),
    )
    calls = _fake_subprocess_run(monkeypatch)
    monkeypatch.setattr("src.worker.deploy.get_pull_request_state", lambda pr_number: _state(merged=False))
    monkeypatch.setattr(
        "src.worker.deploy.merge_pull_request",
        lambda *a, **k: (_ for _ in ()).throw(GitHubClientError("head sha mismatch")),
    )

    result = merge_and_deploy(pr_number="83", pr_url="https://github.com/org/repo/pull/83")

    assert result.success is False
    assert result.merged is False
    assert result.restarted == []
    assert "Merge failed" in result.message
    assert sync_calls == []  # never reached the sync step
    assert calls == []  # no restart attempted


def test_merge_and_deploy_sync_failure_skips_restart(monkeypatch):
    """The bug this guards against: restarting a service without first syncing
    the live checkout just restarts stale code while reporting success."""
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "svc.service")
    _stub_sync(monkeypatch, ok=False, detail="live checkout has uncommitted changes; refusing to overwrite them")
    calls = _fake_subprocess_run(monkeypatch)
    monkeypatch.setattr("src.worker.deploy.get_pull_request_state", lambda pr_number: _state(merged=False))
    monkeypatch.setattr(
        "src.worker.deploy.merge_pull_request",
        lambda pr_number, *, sha, merge_method=None: {"merged": True, "sha": "def456", "message": "Merged"},
    )

    result = merge_and_deploy(pr_number="83", pr_url="https://github.com/org/repo/pull/83")

    assert result.success is False
    assert result.merged is True  # the GitHub merge did happen
    assert result.merge_sha == "def456"
    assert result.restarted == []
    assert "uncommitted changes" in result.message
    assert calls == []  # never attempted a restart against unsynced code


def test_merge_and_deploy_restart_failure_reports_partial_success(monkeypatch):
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "svc.service")
    monkeypatch.setattr(settings, "builder_deploy_self_unit", "")
    _stub_sync(monkeypatch)
    _fake_subprocess_run(monkeypatch, restart_ok=False)
    monkeypatch.setattr("src.worker.deploy.get_pull_request_state", lambda pr_number: _state(merged=False))
    monkeypatch.setattr(
        "src.worker.deploy.merge_pull_request",
        lambda pr_number, *, sha, merge_method=None: {"merged": True, "sha": "def456", "message": "Merged"},
    )

    result = merge_and_deploy(pr_number="83", pr_url="https://github.com/org/repo/pull/83")

    assert result.merged is True  # the merge itself succeeded
    assert result.success is False  # but the deploy overall did not
    assert result.restarted[0][1] is False
    assert "failed to restart" in result.message


def test_merge_and_deploy_without_configured_units_is_a_safe_noop(monkeypatch):
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "")
    monkeypatch.setattr(
        "src.worker.deploy.get_pull_request_state",
        lambda pr_number: (_ for _ in ()).throw(AssertionError("must not call GitHub without units configured")),
    )

    result = merge_and_deploy(pr_number="83", pr_url="https://github.com/org/repo/pull/83")

    assert result.success is False
    assert "BUILDER_DEPLOY_RESTART_UNITS" in result.message


def test_merge_and_deploy_closed_unmerged_pr_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "svc.service")
    monkeypatch.setattr(
        "src.worker.deploy.get_pull_request_state",
        lambda pr_number: _state(merged=False, state="closed"),
    )

    result = merge_and_deploy(pr_number="83", pr_url="https://github.com/org/repo/pull/83")

    assert result.success is False
    assert "closed" in result.message


def test_merge_and_deploy_unresolvable_pr_reports_the_error(monkeypatch):
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "svc.service")
    monkeypatch.setattr(
        "src.worker.deploy.get_pull_request_state",
        lambda pr_number: (_ for _ in ()).throw(GitHubClientError("network error")),
    )

    result = merge_and_deploy(pr_number="83", pr_url="https://github.com/org/repo/pull/83")

    assert result.success is False
    assert "Could not resolve PR" in result.message


def test_merge_and_deploy_defers_self_unit_restart(monkeypatch):
    """If the process running this code is itself one of the restart units,
    restarting it here would kill the process before the caller can finalize
    the deployment record/Slack card. That unit must be deferred, not
    restarted inline."""
    monkeypatch.setattr(
        settings, "builder_deploy_restart_units", "builder-worker.service,knowledge-management-by-slack.service"
    )
    monkeypatch.setattr(settings, "builder_deploy_self_unit", "knowledge-management-by-slack.service")
    _stub_sync(monkeypatch)
    calls = _fake_subprocess_run(monkeypatch)
    monkeypatch.setattr("src.worker.deploy.get_pull_request_state", lambda pr_number: _state(merged=False))
    monkeypatch.setattr(
        "src.worker.deploy.merge_pull_request",
        lambda pr_number, *, sha, merge_method=None: {"merged": True, "sha": "def456", "message": "Merged"},
    )

    result = merge_and_deploy(pr_number="83", pr_url="https://github.com/org/repo/pull/83")

    assert result.pending_self_restart == "knowledge-management-by-slack.service"
    assert [unit for unit, *_ in result.restarted] == ["builder-worker.service"]
    assert "knowledge-management-by-slack.service" not in [c[-1] for c in calls if c[0] == "sudo"]
    assert result.success is True  # non-self units healthy; self-restart handled by the caller
    assert "Restarting knowledge-management-by-slack.service now" in result.message


def test_merge_and_deploy_self_unit_not_in_restart_list_is_ignored(monkeypatch):
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "svc.service")
    monkeypatch.setattr(settings, "builder_deploy_self_unit", "some-other-unit.service")
    _stub_sync(monkeypatch)
    _fake_subprocess_run(monkeypatch)
    monkeypatch.setattr("src.worker.deploy.get_pull_request_state", lambda pr_number: _state(merged=False))
    monkeypatch.setattr(
        "src.worker.deploy.merge_pull_request",
        lambda pr_number, *, sha, merge_method=None: {"merged": True, "sha": "def456", "message": "Merged"},
    )

    result = merge_and_deploy(pr_number="83", pr_url="https://github.com/org/repo/pull/83")

    assert result.pending_self_restart is None
    assert [unit for unit, *_ in result.restarted] == ["svc.service"]


def test_trigger_pending_self_restart_does_not_block(monkeypatch):
    popen_calls = []

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            popen_calls.append((cmd, kwargs))

    monkeypatch.setattr("src.worker.deploy.subprocess.Popen", _FakePopen)

    trigger_pending_self_restart("knowledge-management-by-slack.service")

    assert popen_calls
    cmd, kwargs = popen_calls[0]
    assert cmd == ["sudo", "systemctl", "restart", "knowledge-management-by-slack.service"]


# --- _sync_checkout: real throwaway git repos, no mocking of git itself ---


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial"], check=True, capture_output=True)


def _commit(path: Path, message: str) -> str:
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", message], check=True, capture_output=True
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _head(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def deploy_repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)

    seed = tmp_path / "seed"
    _init_repo(seed)
    subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "origin", "main"], check=True, capture_output=True)

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", str(origin), str(checkout)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Test"], check=True)

    monkeypatch.setattr(settings, "builder_git_remote", "origin")
    monkeypatch.setattr(settings, "builder_base_branch", "main")
    return {"origin": origin, "seed": seed, "checkout": checkout}


def test_sync_checkout_rejects_non_git_directory(tmp_path):
    ok, detail = _sync_checkout(tmp_path / "not-a-repo", "deadbeef")

    assert ok is False
    assert "not a git checkout" in detail


def test_sync_checkout_refuses_dirty_checkout(deploy_repo):
    checkout = deploy_repo["checkout"]
    (checkout / "dirty.txt").write_text("uncommitted\n")

    ok, detail = _sync_checkout(checkout, "deadbeef")

    assert ok is False
    assert "uncommitted changes" in detail


def test_sync_checkout_is_a_noop_when_already_at_target(deploy_repo):
    checkout = deploy_repo["checkout"]

    ok, detail = _sync_checkout(checkout, _head(checkout))

    assert ok is True
    assert "already at" in detail


def test_sync_checkout_fast_forwards_to_the_merged_commit(deploy_repo):
    seed = deploy_repo["seed"]
    checkout = deploy_repo["checkout"]
    new_sha = _commit(seed, "merged change")
    subprocess.run(["git", "-C", str(seed), "push", "origin", "main"], check=True, capture_output=True)

    ok, detail = _sync_checkout(checkout, new_sha)

    assert ok is True
    assert _head(checkout) == new_sha
    assert new_sha[:12] in detail


def test_sync_checkout_refuses_a_diverged_checkout(deploy_repo):
    seed = deploy_repo["seed"]
    checkout = deploy_repo["checkout"]
    _commit(checkout, "local-only change never pushed")
    new_sha = _commit(seed, "merged change")
    subprocess.run(["git", "-C", str(seed), "push", "origin", "main"], check=True, capture_output=True)

    ok, detail = _sync_checkout(checkout, new_sha)

    assert ok is False
    assert "diverged" in detail

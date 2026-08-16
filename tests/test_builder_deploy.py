"""Tests for the deterministic Merge & Deploy path (src/worker/deploy.py)."""

from __future__ import annotations

import subprocess

from src.core.config import settings
from src.integrations.github_client import GitHubClientError
from src.worker.deploy import merge_and_deploy


def _state(*, merged: bool, state: str = "open", head_sha: str = "abc123"):
    return {"number": "83", "state": state, "merged": merged, "head_sha": head_sha}


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
    assert merge_calls == [("83", "abc123")]
    assert result.restarted == [("knowledge-management-by-slack.service", True, "active")]
    assert any(call[0] == "sudo" for call in calls)


def test_merge_and_deploy_is_idempotent_when_already_merged(monkeypatch):
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "svc.service")
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


def test_merge_and_deploy_merge_failure_skips_restart(monkeypatch):
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "svc.service")
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
    assert calls == []  # no restart attempted


def test_merge_and_deploy_restart_failure_reports_partial_success(monkeypatch):
    monkeypatch.setattr(settings, "builder_deploy_restart_units", "svc.service")
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

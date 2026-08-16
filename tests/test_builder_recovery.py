"""Tests for Builder worker crash/stuck-task recovery on startup."""

from pathlib import Path

import pytest

from src.core.config import settings
from src.worker.builder_worker import _recover_on_startup, run_forever


class RecordingStore:
    def __init__(self, stuck_tasks):
        self._stuck_tasks = stuck_tasks
        self.published: list[dict] = []

    def recover_stuck_tasks(self):
        return self._stuck_tasks

    def get(self, task_id):
        return {"task_id": task_id, "progress_message_ts": None}

    def set_progress_message_ts(self, task_id, message_ts):
        return None


def test_recover_on_startup_prunes_worktrees_and_fails_stuck_tasks(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "builder_workdir", tmp_path)
    pruned = []
    cleaned_up = []
    published = []

    monkeypatch.setattr("src.worker.builder_worker.prune_orphaned_worktrees", lambda: pruned.append(True))
    monkeypatch.setattr(
        "src.worker.builder_worker.cleanup_worktree",
        lambda worktree: cleaned_up.append(worktree.branch_name),
    )
    monkeypatch.setattr(
        "src.worker.builder_worker._publish_status",
        lambda store, task, **kwargs: published.append((task["task_id"], kwargs)),
    )

    stuck = [
        {
            "task_id": "bld_stuck1",
            "channel_id": "C1",
            "thread_ts": None,
            "branch_name": "builder/bld_stuck1",
            "pr_url": None,
        },
        {
            "task_id": "bld_stuck2",
            "channel_id": "C1",
            "thread_ts": None,
            "branch_name": None,
            "pr_url": None,
        },
    ]
    store = RecordingStore(stuck)

    _recover_on_startup(store)

    assert pruned == [True]
    assert cleaned_up == ["builder/bld_stuck1"]  # no worktree cleanup attempted without a branch
    assert [task_id for task_id, _ in published] == ["bld_stuck1", "bld_stuck2"]
    assert all(kwargs["status"] == "failed" for _, kwargs in published)


def test_recover_on_startup_is_a_noop_when_nothing_stuck(monkeypatch):
    cleaned_up = []
    published = []
    monkeypatch.setattr("src.worker.builder_worker.prune_orphaned_worktrees", lambda: None)
    monkeypatch.setattr("src.worker.builder_worker.cleanup_worktree", lambda worktree: cleaned_up.append(worktree))
    monkeypatch.setattr(
        "src.worker.builder_worker._publish_status",
        lambda store, task, **kwargs: published.append(task),
    )

    _recover_on_startup(RecordingStore([]))

    assert cleaned_up == []
    assert published == []


class _StopLoop(Exception):
    """Sentinel used to escape run_forever's infinite loop after one pass."""


def test_run_forever_checks_secrets_and_recovers_before_polling(monkeypatch):
    calls: list[str] = []

    class FakeStore:
        def claim_next(self):
            calls.append("claim_next")
            raise _StopLoop

    monkeypatch.setattr(settings, "builder_secret_check_enabled", True)
    monkeypatch.setattr(settings, "builder_startup_recovery_enabled", True)
    monkeypatch.setattr("src.worker.builder_worker.BuilderTaskStore", lambda: FakeStore())
    monkeypatch.setattr(
        "src.worker.builder_worker.check_secret_exposure",
        lambda: (calls.append("check_secret_exposure"), [])[1],
    )
    monkeypatch.setattr(
        "src.worker.builder_worker.log_and_warn_findings",
        lambda findings: calls.append("log_and_warn_findings"),
    )
    monkeypatch.setattr(
        "src.worker.builder_worker._recover_on_startup",
        lambda store: calls.append("_recover_on_startup"),
    )

    with pytest.raises(_StopLoop):
        run_forever()

    assert calls == [
        "check_secret_exposure",
        "log_and_warn_findings",
        "_recover_on_startup",
        "claim_next",
    ]


def test_run_forever_skips_checks_when_disabled(monkeypatch):
    calls: list[str] = []

    class FakeStore:
        def claim_next(self):
            calls.append("claim_next")
            raise _StopLoop

    monkeypatch.setattr(settings, "builder_secret_check_enabled", False)
    monkeypatch.setattr(settings, "builder_startup_recovery_enabled", False)
    monkeypatch.setattr("src.worker.builder_worker.BuilderTaskStore", lambda: FakeStore())
    monkeypatch.setattr(
        "src.worker.builder_worker.check_secret_exposure",
        lambda: calls.append("check_secret_exposure"),
    )
    monkeypatch.setattr(
        "src.worker.builder_worker._recover_on_startup",
        lambda store: calls.append("_recover_on_startup"),
    )

    with pytest.raises(_StopLoop):
        run_forever()

    assert calls == ["claim_next"]

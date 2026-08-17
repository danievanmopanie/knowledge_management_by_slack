"""Tests for the Builder worker validation/repair and PR handoff loop."""

from pathlib import Path

from src.core.config import settings
from src.worker.aider_runner import AiderResult
from src.worker.builder_worker import _repair_prompt, _run_task, _validate_and_repair
from src.worker.validation import ValidationResult
from src.worker.workspace import Worktree


class FakeStore:
    def get(self, task_id):
        return {"task_id": task_id, "progress_message_ts": None}

    def set_progress_message_ts(self, task_id, message_ts):
        return None


class RecordingStore(FakeStore):
    def __init__(self):
        self.running = []
        self.succeeded = []
        self.failed = []

    def mark_running(self, task_id, *, branch_name):
        self.running.append((task_id, branch_name))

    def mark_succeeded(self, task_id, *, pr_url=None, result_text=None):
        self.succeeded.append((task_id, pr_url, result_text))

    def mark_failed(self, task_id, *, error_message):
        self.failed.append((task_id, error_message))


def _validation(success: bool, output: str = "") -> ValidationResult:
    return ValidationResult(
        success=success,
        command="python -m pytest -q",
        stdout=output,
        stderr="",
        returncode=0 if success else 1,
    )


def _aider_success() -> AiderResult:
    return AiderResult(success=True, stdout="done", stderr="", returncode=0)


def test_validation_failure_is_sent_back_for_repair(monkeypatch, tmp_path: Path):
    results = iter([_validation(False, "FAILED test_widget"), _validation(True, "passed")])
    repair_goals: list[str] = []
    statuses: list[dict] = []

    monkeypatch.setattr(settings, "builder_max_repair_attempts", 2)
    monkeypatch.setattr(
        "src.worker.builder_worker.run_validation",
        lambda *, worktree_path: next(results),
    )

    def fake_aider(*, goal, worktree_path):
        repair_goals.append(goal)
        return _aider_success()

    monkeypatch.setattr("src.worker.builder_worker.run_aider", fake_aider)
    monkeypatch.setattr(
        "src.worker.builder_worker._publish_status",
        lambda store, task, **kwargs: statuses.append(kwargs),
    )

    task = {"task_id": "bld_test", "goal": "add widget", "channel_id": "C1"}
    worktree = Worktree(task_id="bld_test", branch_name="builder/bld_test", path=tmp_path)

    result, attempts = _validate_and_repair(FakeStore(), task, worktree)

    assert result.success is True
    assert attempts == 1
    assert len(repair_goals) == 1
    assert "FAILED test_widget" in repair_goals[0]
    assert "Do not remove, skip, weaken, or xfail tests" in repair_goals[0]
    # BackgroundActivity now emits an initial running heartbeat before repair.
    assert any(status["status"] == "repairing" for status in statuses)
    assert statuses[-1]["status"] == "validated"


def test_repair_loop_stops_at_configured_limit(monkeypatch, tmp_path: Path):
    validation_calls = 0

    def always_fail(*, worktree_path):
        nonlocal validation_calls
        validation_calls += 1
        return _validation(False, "still failing")

    monkeypatch.setattr(settings, "builder_max_repair_attempts", 2)
    monkeypatch.setattr("src.worker.builder_worker.run_validation", always_fail)
    monkeypatch.setattr(
        "src.worker.builder_worker.run_aider",
        lambda *, goal, worktree_path: _aider_success(),
    )
    monkeypatch.setattr("src.worker.builder_worker._publish_status", lambda store, task, **kwargs: None)

    task = {"task_id": "bld_test", "goal": "add widget", "channel_id": "C1"}
    worktree = Worktree(task_id="bld_test", branch_name="builder/bld_test", path=tmp_path)

    result, attempts = _validate_and_repair(FakeStore(), task, worktree)

    assert result.success is False
    assert attempts == 2
    assert validation_calls == 3


def test_repair_prompt_preserves_original_goal_and_failure():
    prompt = _repair_prompt("keep API compatibility", _validation(False, "boom"), 1)

    assert "keep API compatibility" in prompt
    assert "boom" in prompt
    assert "python -m pytest -q" in prompt


def _handoff_pr():
    return {
        "number": "83",
        "html_url": "https://github.com/org/repo/pull/83",
        "title": "External change",
        "body": "Created by another coding assistant",
        "head_ref": "feature/external",
        "head_sha": "abc",
        "base_ref": "main",
        "state": "open",
        "head_repo_full_name": "org/repo",
    }


def _handoff_task():
    return {
        "task_id": "bld_handoff",
        "goal": "Take PR #83 and action it on the GX10.",
        "channel_id": "C1",
        "thread_ts": "123.4",
        "handoff_pr_number": "83",
        "continuation_branch": None,
        "continuation_pr_url": None,
    }


def test_external_pr_handoff_validates_even_when_no_edit_is_required(monkeypatch, tmp_path: Path):
    store = RecordingStore()
    worktree = Worktree(
        task_id="bld_handoff",
        branch_name="feature/external",
        path=tmp_path,
        start_sha="abc",
    )
    validation_calls = []
    pushes = []

    monkeypatch.setattr("src.worker.builder_worker.get_pull_request", lambda number: _handoff_pr())
    monkeypatch.setattr(
        "src.worker.builder_worker.prepare_worktree",
        lambda task_id, continuation_branch=None: worktree,
    )
    monkeypatch.setattr(
        "src.worker.builder_worker._execute_primary_harness",
        lambda *, goal, worktree: "Executed the handed-off PR locally.",
    )
    monkeypatch.setattr("src.worker.builder_worker.has_repository_changes", lambda worktree: False)

    def fake_validate(store_arg, task_arg, worktree_arg):
        validation_calls.append((store_arg, task_arg, worktree_arg))
        return _validation(True, "passed"), 0

    monkeypatch.setattr("src.worker.builder_worker._validate_and_repair", fake_validate)
    monkeypatch.setattr("src.worker.builder_worker.push_branch", lambda worktree: pushes.append(worktree))
    monkeypatch.setattr("src.worker.builder_worker.cleanup_worktree", lambda worktree: None)
    monkeypatch.setattr("src.worker.builder_worker._publish_status", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.worker.builder_worker.publish_report_to_channel", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "src.worker.builder_worker.create_pull_request",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not create a new PR")),
    )

    _run_task(store, _handoff_task())

    assert len(validation_calls) == 1
    assert pushes == []
    assert store.failed == []
    assert store.succeeded == [
        ("bld_handoff", "https://github.com/org/repo/pull/83", "Executed the handed-off PR locally.")
    ]


def test_external_pr_handoff_pushes_repairs_to_same_branch_without_new_pr(monkeypatch, tmp_path: Path):
    store = RecordingStore()
    worktree = Worktree(
        task_id="bld_handoff",
        branch_name="feature/external",
        path=tmp_path,
        start_sha="abc",
    )
    pushes = []
    commits = []

    monkeypatch.setattr("src.worker.builder_worker.get_pull_request", lambda number: _handoff_pr())
    monkeypatch.setattr(
        "src.worker.builder_worker.prepare_worktree",
        lambda task_id, continuation_branch=None: worktree,
    )
    monkeypatch.setattr(
        "src.worker.builder_worker._execute_primary_harness",
        lambda *, goal, worktree: "Found and repaired a runtime problem.",
    )
    monkeypatch.setattr("src.worker.builder_worker.has_repository_changes", lambda worktree: True)
    monkeypatch.setattr(
        "src.worker.builder_worker._validate_and_repair",
        lambda store_arg, task_arg, worktree_arg: (_validation(True, "passed"), 0),
    )
    monkeypatch.setattr(
        "src.worker.builder_worker.commit_pending_changes",
        lambda worktree, *, message: commits.append((worktree.branch_name, message)),
    )
    monkeypatch.setattr("src.worker.builder_worker.push_branch", lambda worktree: pushes.append(worktree.branch_name))
    monkeypatch.setattr("src.worker.builder_worker.cleanup_worktree", lambda worktree: None)
    monkeypatch.setattr("src.worker.builder_worker._publish_status", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.worker.builder_worker.publish_report_to_channel", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "src.worker.builder_worker.create_pull_request",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must update the same PR")),
    )

    _run_task(store, _handoff_task())

    assert pushes == ["feature/external"]
    assert commits and commits[0][0] == "feature/external"
    assert store.failed == []
    assert store.succeeded[0][1] == "https://github.com/org/repo/pull/83"

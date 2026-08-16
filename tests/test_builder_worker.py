"""Tests for the Builder worker validation/repair loop."""

from pathlib import Path

from src.core.config import settings
from src.worker.aider_runner import AiderResult
from src.worker.builder_worker import _repair_prompt, _validate_and_repair
from src.worker.validation import ValidationResult
from src.worker.workspace import Worktree


class FakeStore:
    def get(self, task_id):
        return {"task_id": task_id, "progress_message_ts": None}

    def set_progress_message_ts(self, task_id, message_ts):
        return None


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
    assert statuses[0]["status"] == "repairing"
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

"""Tests for the Builder Agent local validation gate."""

from pathlib import Path

from src.core.config import settings
from src.worker import validation


def test_resolved_test_command_uses_worker_python(monkeypatch):
    monkeypatch.setattr(settings, "builder_test_command", "{python} -m pytest -q")

    command = validation.resolved_test_command()

    assert "-m pytest -q" in command
    assert "{python}" not in command


def test_validation_runs_configured_command(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "builder_test_command", "printf ok")
    monkeypatch.setattr(settings, "builder_test_timeout_seconds", 10)

    result = validation.run_validation(worktree_path=tmp_path)

    assert result.success is True
    assert result.returncode == 0
    assert result.stdout == "ok"


def test_empty_validation_command_fails_when_required(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "builder_test_command", "")
    monkeypatch.setattr(settings, "builder_require_tests_pass", True)

    result = validation.run_validation(worktree_path=tmp_path)

    assert result.success is False
    assert result.returncode == -2
    assert "tests are required" in result.stderr


def test_validation_output_is_bounded(monkeypatch):
    monkeypatch.setattr(settings, "builder_validation_output_chars", 5)
    result = validation.ValidationResult(
        success=False,
        command="pytest",
        stdout="123456789",
        stderr="",
        returncode=1,
    )

    assert result.output == "56789"

"""Tests for the Builder startup secret-exposure self-check."""

from pathlib import Path

from src.core.config import settings
from src.worker.secret_exposure_check import check_secret_exposure


def test_check_secret_exposure_flags_a_readable_env_file(monkeypatch, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".env").write_text("GITHUB_TOKEN=leaked\n")
    monkeypatch.setattr(settings, "builder_repo_path", repo_path)
    monkeypatch.setattr(settings, "builder_secret_check_extra_paths", "")
    monkeypatch.chdir(tmp_path)  # keep cwd's own .env probe from also matching
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nonexistent-home"))

    findings = check_secret_exposure()

    paths = {finding.path for finding in findings}
    assert str((repo_path / ".env").resolve()) in paths


def test_check_secret_exposure_returns_empty_when_nothing_readable(monkeypatch, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setattr(settings, "builder_repo_path", repo_path)
    monkeypatch.setattr(settings, "builder_secret_check_extra_paths", "")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nonexistent-home"))

    assert check_secret_exposure() == []


def test_check_secret_exposure_includes_extra_configured_paths(monkeypatch, tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    extra = tmp_path / "credentials.json"
    extra.write_text("{}")
    monkeypatch.setattr(settings, "builder_repo_path", repo_path)
    monkeypatch.setattr(settings, "builder_secret_check_extra_paths", str(extra))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nonexistent-home"))

    findings = check_secret_exposure()

    assert any(finding.path == str(extra.resolve()) for finding in findings)

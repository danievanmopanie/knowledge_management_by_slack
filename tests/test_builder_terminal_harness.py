"""Tests for the Builder's real GX10 terminal/tool harness."""

import shlex
import sys
from pathlib import Path

from src.core.config import settings
from src.worker.terminal_harness import run_shell, run_terminal_harness


def _terminal_settings(monkeypatch):
    monkeypatch.setattr(settings, "builder_terminal_enabled", True)
    monkeypatch.setattr(settings, "builder_terminal_command_timeout_seconds", 10)
    monkeypatch.setattr(settings, "builder_terminal_max_command_timeout_seconds", 30)
    monkeypatch.setattr(settings, "builder_terminal_output_chars", 4000)
    monkeypatch.setattr(settings, "builder_terminal_allow_docker_run", False)


def test_run_shell_executes_real_command_in_worktree(monkeypatch, tmp_path: Path):
    _terminal_settings(monkeypatch)

    output = run_shell("pwd && printf 'builder-terminal-ok'", worktree_path=tmp_path)

    assert "exit_code=0" in output
    assert str(tmp_path) in output
    assert "builder-terminal-ok" in output


def test_run_shell_scrubs_github_token_from_child_environment(monkeypatch, tmp_path: Path):
    _terminal_settings(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "do-not-leak")

    python = shlex.quote(sys.executable)
    output = run_shell(
        f"{python} -c \"import os; print(os.getenv('GITHUB_TOKEN'))\"",
        worktree_path=tmp_path,
    )

    assert "do-not-leak" not in output
    assert "None" in output


def test_run_shell_blocks_privilege_escalation_and_secret_paths(monkeypatch, tmp_path: Path):
    _terminal_settings(monkeypatch)

    assert "BLOCKED" in run_shell("sudo systemctl restart something", worktree_path=tmp_path)
    assert "BLOCKED" in run_shell("cat /opt/app/.env", worktree_path=tmp_path)
    assert "BLOCKED" in run_shell("cat ~/.ssh/id_ed25519", worktree_path=tmp_path)


def test_run_shell_blocks_docker_run_by_default(monkeypatch, tmp_path: Path):
    _terminal_settings(monkeypatch)

    output = run_shell("docker run --rm alpine true", worktree_path=tmp_path)

    assert "BLOCKED" in output
    assert "docker run/create is disabled" in output


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses, **_kwargs):
        self.responses = iter(responses)
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url, headers, json):
        self.posts.append((url, headers, json))
        return next(self.responses)


def test_terminal_harness_executes_tool_and_returns_followup_answer(monkeypatch, tmp_path: Path):
    _terminal_settings(monkeypatch)
    monkeypatch.setattr(settings, "builder_terminal_max_steps", 4)
    monkeypatch.setattr(settings, "builder_terminal_max_tokens", 256)
    monkeypatch.setattr(settings, "builder_terminal_temperature", 0.0)
    monkeypatch.setattr(settings, "builder_llm_model", "aeon-builder")
    monkeypatch.setattr(settings, "builder_llm_base_url", "http://atlas.test/v1")
    monkeypatch.setattr(settings, "builder_llm_api_key", "local")

    responses = [
        _FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "run_shell",
                                        "arguments": '{"command":"printf tool-ok"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ),
        _FakeResponse(
            {
                "choices": [
                    {"message": {"content": "I ran it on the GX10 and it returned tool-ok."}}
                ]
            }
        ),
    ]
    fake = _FakeClient(responses)
    monkeypatch.setattr(
        "src.worker.terminal_harness.httpx.Client",
        lambda **kwargs: fake,
    )

    result = run_terminal_harness(goal="Check the local shell", worktree_path=tmp_path)

    assert result.success is True
    assert result.tool_calls == 1
    assert "tool-ok" in result.answer
    second_payload = fake.posts[1][2]
    tool_messages = [m for m in second_payload["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert "tool-ok" in tool_messages[0]["content"]

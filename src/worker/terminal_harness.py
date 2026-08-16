"""Model-driven terminal/tool loop for the on-device Builder harness.

The Builder model does not merely emit shell snippets for a human to copy. When
this harness is enabled, the model can call a real shell tool on the GX10 and a
code-editing tool backed by Aider. Tool outputs are returned to the model so it
can inspect, edit, execute, diagnose and repeat within one Slack turn.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from src.core.config import settings
from src.worker.aider_runner import run_aider

logger = logging.getLogger(__name__)


@dataclass
class TerminalHarnessResult:
    success: bool
    answer: str
    tool_calls: int
    error: str = ""


_RUN_SHELL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": (
            "Run a real non-interactive shell command on the GX10 as the Builder service account. "
            "Use this to inspect the repository, run tests, start/check local development services, "
            "query Docker, inspect logs, curl local endpoints, and gather real runtime evidence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute on the GX10.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 900,
                    "description": "Optional command timeout. Use short timeouts unless required.",
                },
            },
            "required": ["command"],
        },
    },
}

_EDIT_CODE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "edit_code",
        "description": (
            "Make repository edits in the current checked-out worktree using the coding editor. "
            "Give a complete implementation instruction based on evidence already gathered."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Precise code/config/test/documentation changes to implement.",
                }
            },
            "required": ["instruction"],
        },
    },
}

# These are defence-in-depth guards, not the primary security boundary. The
# primary boundary is the dedicated unprivileged service account and filesystem
# permissions. Commands are also executed with a scrubbed environment.
_BLOCKED_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|[;&|]\s*)\s*(sudo|doas|pkexec)\b", re.I), "privilege escalation is disabled"),
    (re.compile(r"(^|[;&|]\s*)\s*su\s+", re.I), "switching users is disabled"),
    (re.compile(r"\b(shutdown|reboot|poweroff|halt)\b", re.I), "host power operations are disabled"),
    (re.compile(r"\bmkfs(?:\.|\s)|\bfdisk\b|\bparted\b", re.I), "disk formatting/partitioning is disabled"),
    (re.compile(r"\bdd\s+.*\bof=/dev/", re.I), "raw device writes are disabled"),
    (re.compile(r"\brm\s+-[^\n]*r[^\n]*f[^\n]*\s+/(?:\s|$)", re.I), "destructive root deletion is disabled"),
    (re.compile(r"(?:^|[\s/])\.env(?:\s|$|/)", re.I), "reading deployment .env files is disabled"),
    (re.compile(r"(?:^|[\s/])\.ssh(?:\s|$|/)", re.I), "reading SSH credential directories is disabled"),
    (re.compile(r"/etc/shadow\b", re.I), "reading password hashes is disabled"),
    (re.compile(r"/proc/[^\s]*/environ\b", re.I), "reading process environments is disabled"),
    (re.compile(r"\b(id_rsa|id_ed25519|credentials\.json)\b", re.I), "reading credential material is disabled"),
)

_DOCKER_RUN_RE = re.compile(r"(^|[;&|]\s*)\s*docker\s+(run|create)\b", re.I)


def _system_prompt(worktree_path: Path) -> str:
    return f"""You are the resident Builder engineering harness running locally on the user's GX10.

You have REAL tools. Do not tell the user to copy/paste commands into a terminal. When evidence can be gathered on this device, use run_shell yourself. When repository files need to change, use edit_code, then inspect and execute the result yourself.

Current repository worktree: {worktree_path}

Operating rules:
- Treat the user's Slack request as the engineering goal, not as a shell command.
- Inspect before guessing. Use run_shell for git diff/status/log, tests, Python, curl, local service checks, Docker compose/ps/logs/inspect/exec, and other non-interactive engineering commands.
- Use edit_code for substantive file edits instead of fragile sed/cat rewrites.
- After an edit, run relevant focused checks yourself. The worker will still run a deterministic final validation gate before publishing.
- For an existing pull request, validate the code already on that PR branch and repair that same branch when needed.
- Never claim a command ran or a test passed unless a tool result proves it.
- Never request or expose secrets. Do not read .env, SSH keys, credentials, process environments, or password stores.
- Do not use sudo or attempt privilege escalation. You have exactly the permissions of the Builder service account.
- Avoid destructive host operations. Do not reboot, format disks, delete system roots, or weaken tests merely to obtain green output.
- Docker access, if the service account has it, is powerful. Prefer docker compose, ps, logs, inspect and exec; do not create privileged/root-mounted containers.
- Keep iterating until the request is resolved or you can clearly identify the blocking permission/runtime fact.
- End with a concise natural-language summary of what you inspected, what you changed, what you actually ran, and any remaining blocker.
"""


def _safe_shell_environment() -> dict[str, str]:
    """Return a shell environment without Slack/GitHub/model credentials."""
    allowed_prefixes = ("CUDA_", "NVIDIA_", "HF_", "TRANSFORMERS_", "PYTHON")
    allowed_exact = {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "TERM",
        "TMPDIR",
        "VIRTUAL_ENV",
    }
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in allowed_exact or key.startswith(allowed_prefixes):
            env[key] = value
    return env


def _validate_shell_command(command: str) -> str | None:
    if not command.strip():
        return "empty commands are not allowed"
    for pattern, reason in _BLOCKED_COMMAND_PATTERNS:
        if pattern.search(command):
            return reason
    if not settings.builder_terminal_allow_docker_run and _DOCKER_RUN_RE.search(command):
        return "docker run/create is disabled; use existing services or docker compose instead"
    return None


def run_shell(command: str, *, worktree_path: Path, timeout_seconds: int | None = None) -> str:
    """Execute a real shell command on the GX10 and return bounded stdout/stderr."""
    if not settings.builder_terminal_enabled:
        return "BLOCKED: Builder terminal execution is disabled by configuration."

    blocked = _validate_shell_command(command)
    if blocked:
        return f"BLOCKED: {blocked}."

    timeout = timeout_seconds or settings.builder_terminal_command_timeout_seconds
    timeout = max(1, min(int(timeout), settings.builder_terminal_max_command_timeout_seconds))
    logger.info("Builder shell: %s (cwd=%s)", command, worktree_path)
    try:
        result = subprocess.run(
            ["/bin/bash", "-lc", command],
            cwd=worktree_path,
            env=_safe_shell_environment(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        captured = "\n".join(part for part in (exc.stdout or "", exc.stderr or "") if part)
        return _bounded(f"TIMEOUT after {timeout}s\n{captured}")
    except OSError as exc:
        return f"EXECUTION ERROR: {exc}"

    output = (
        f"exit_code={result.returncode}\n"
        f"stdout:\n{result.stdout or '(empty)'}\n"
        f"stderr:\n{result.stderr or '(empty)'}"
    )
    return _bounded(output)


def _bounded(text: str) -> str:
    limit = max(1000, settings.builder_terminal_output_chars)
    if len(text) <= limit:
        return text
    return "[...output truncated; showing tail...]\n" + text[-limit:]


def _edit_code(instruction: str, *, worktree_path: Path) -> str:
    result = run_aider(goal=instruction, worktree_path=worktree_path)
    combined = (
        f"aider_success={result.success}\n"
        f"return_code={result.returncode}\n"
        f"stdout:\n{result.stdout or '(empty)'}\n"
        f"stderr:\n{result.stderr or '(empty)'}"
    )
    return _bounded(combined)


def _tool_result(tool_call: dict[str, Any], *, worktree_path: Path) -> str:
    function = tool_call.get("function") or {}
    name = function.get("name") or ""
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError as exc:
        return f"TOOL ARGUMENT ERROR: {exc}"

    if name == "run_shell":
        return run_shell(
            str(arguments.get("command") or ""),
            worktree_path=worktree_path,
            timeout_seconds=arguments.get("timeout_seconds"),
        )
    if name == "edit_code":
        instruction = str(arguments.get("instruction") or "").strip()
        if not instruction:
            return "TOOL ARGUMENT ERROR: edit_code requires a non-empty instruction."
        return _edit_code(instruction, worktree_path=worktree_path)
    return f"UNKNOWN TOOL: {name}"


def run_terminal_harness(*, goal: str, worktree_path: Path) -> TerminalHarnessResult:
    """Run a bounded multi-turn tool loop against the Builder-only Atlas model."""
    if not settings.builder_terminal_enabled:
        return TerminalHarnessResult(
            success=False,
            answer="",
            tool_calls=0,
            error="Builder terminal harness is disabled.",
        )

    endpoint = settings.builder_llm_base_url.rstrip("/") + "/chat/completions"
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(worktree_path)},
        {"role": "user", "content": goal},
    ]
    tool_calls = 0

    try:
        with httpx.Client(timeout=settings.builder_task_timeout_seconds) as client:
            for _step in range(settings.builder_terminal_max_steps):
                response = client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {settings.builder_llm_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.builder_llm_model,
                        "messages": messages,
                        "tools": [_RUN_SHELL_TOOL, _EDIT_CODE_TOOL],
                        "tool_choice": "auto",
                        "temperature": settings.builder_terminal_temperature,
                        "max_tokens": settings.builder_terminal_max_tokens,
                    },
                )
                if response.status_code >= 400:
                    return TerminalHarnessResult(
                        success=False,
                        answer="",
                        tool_calls=tool_calls,
                        error=f"Atlas tool-loop HTTP {response.status_code}: {response.text[:1000]}",
                    )
                data = response.json()
                choices = data.get("choices") or []
                if not choices:
                    return TerminalHarnessResult(False, "", tool_calls, "Atlas returned no choices.")
                message = choices[0].get("message") or {}
                calls = message.get("tool_calls") or []

                if not calls:
                    answer = str(message.get("content") or "").strip()
                    return TerminalHarnessResult(
                        success=True,
                        answer=answer or "Completed the on-device engineering turn.",
                        tool_calls=tool_calls,
                    )

                # Preserve the assistant tool-call message exactly enough for
                # OpenAI-compatible continuation semantics.
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content"),
                        "tool_calls": calls,
                    }
                )
                for call in calls:
                    tool_calls += 1
                    result = _tool_result(call, worktree_path=worktree_path)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or f"tool-{tool_calls}",
                            "content": result,
                        }
                    )
    except httpx.HTTPError as exc:
        return TerminalHarnessResult(False, "", tool_calls, f"Atlas tool-loop request failed: {exc}")

    return TerminalHarnessResult(
        success=False,
        answer="",
        tool_calls=tool_calls,
        error=(
            "Builder reached the configured terminal tool-step limit before producing a final answer."
        ),
    )

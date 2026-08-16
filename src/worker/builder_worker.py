"""Long-running poll loop: claims queued Builder Agent tasks and executes them."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.agents.builder.task_store import BuilderTaskStore
from src.core.config import settings
from src.integrations.github_client import GitHubClientError, create_pull_request
from src.reporting.publisher import publish_report_to_channel
from src.worker.aider_runner import AiderResult, run_aider
from src.worker.validation import ValidationResult, run_validation
from src.worker.workspace import WorkspaceError, Worktree, cleanup_worktree, prepare_worktree, push_branch

logger = logging.getLogger(__name__)


def run_forever() -> None:
    store = BuilderTaskStore()
    logger.info("Builder worker started, polling every %ss", settings.builder_poll_interval_seconds)
    while True:
        task = store.claim_next()
        if task is None:
            time.sleep(settings.builder_poll_interval_seconds)
            continue
        _run_task(store, task)


def _run_task(store: BuilderTaskStore, task: dict[str, Any]) -> None:
    task_id = task["task_id"]
    logger.info("Claimed builder task %s", task_id)
    worktree: Worktree | None = None
    try:
        worktree = prepare_worktree(task_id)
        store.mark_running(task_id, branch_name=worktree.branch_name)
        _notify(task, f"🛠️ Build task `{task_id}` started on `{worktree.branch_name}`.")

        initial = run_aider(goal=task["goal"], worktree_path=worktree.path)
        _require_aider_success(initial, phase="initial implementation")

        validation, repair_attempts = _validate_and_repair(task, worktree)
        if not validation.success and settings.builder_require_tests_pass:
            raise RuntimeError(_validation_failure_message(validation, repair_attempts))

        # The publish boundary is deliberately after validation. A broken
        # worktree never reaches the remote when tests are configured as required.
        push_branch(worktree)
        validation_label = "passed" if validation.success else "not required"
        pr = create_pull_request(
            branch_name=worktree.branch_name,
            title=f"Builder Agent: {task['goal'][:72]}",
            body=(
                "Automated change requested via Slack Builder Agent.\n\n"
                f"Goal:\n{task['goal']}\n\n"
                f"Task: `{task_id}`\n\n"
                "Local validation:\n"
                f"- Status: **{validation_label}**\n"
                f"- Command: `{validation.command or '(disabled)'}`\n"
                f"- Repair attempts: {repair_attempts}\n\n"
                f"Builder model checkpoint: `{settings.builder_model_checkpoint}`\n"
                f"Inference endpoint: `{settings.builder_llm_base_url}`"
            ),
        )
        store.mark_succeeded(task_id, pr_url=pr["html_url"])
        _notify(
            task,
            f"✅ Build task `{task_id}` complete. Local validation {validation_label}; "
            f"PR: {pr['html_url']}",
        )

    except (WorkspaceError, GitHubClientError, RuntimeError) as exc:
        logger.exception("Builder task %s failed", task_id)
        store.mark_failed(task_id, error_message=str(exc)[:2000])
        _notify(task, f"❌ Build task `{task_id}` failed: {exc}")
    except Exception as exc:
        logger.exception("Builder task %s failed unexpectedly", task_id)
        store.mark_failed(task_id, error_message=str(exc)[:2000])
        _notify(task, f"❌ Build task `{task_id}` failed unexpectedly: {exc}")
    finally:
        if worktree is not None:
            cleanup_worktree(worktree)


def _validate_and_repair(
    task: dict[str, Any], worktree: Worktree
) -> tuple[ValidationResult, int]:
    """Run local validation and let the coding model repair failures in-place."""
    validation = run_validation(worktree_path=worktree.path)
    repair_attempts = 0

    while not validation.success and repair_attempts < settings.builder_max_repair_attempts:
        repair_attempts += 1
        _notify(
            task,
            f"🧪 Validation failed for `{task['task_id']}`. "
            f"AEON repair attempt {repair_attempts}/{settings.builder_max_repair_attempts} is running.",
        )
        repair_goal = _repair_prompt(task["goal"], validation, repair_attempts)
        repair = run_aider(goal=repair_goal, worktree_path=worktree.path)
        _require_aider_success(repair, phase=f"repair attempt {repair_attempts}")
        validation = run_validation(worktree_path=worktree.path)

    if validation.success:
        _notify(
            task,
            f"🧪 Local validation passed for `{task['task_id']}` "
            f"after {repair_attempts} repair attempt(s).",
        )
    return validation, repair_attempts


def _repair_prompt(goal: str, validation: ValidationResult, attempt: int) -> str:
    """Turn deterministic test output into a bounded repair instruction."""
    return (
        "The requested implementation is not yet ready to publish. Fix the code in this "
        "worktree so the required validation passes. Do not remove, skip, weaken, or xfail "
        "tests merely to make the suite green, and do not undo the original requested change.\n\n"
        f"Original goal:\n{goal}\n\n"
        f"Repair attempt: {attempt}\n"
        f"Validation command: {validation.command}\n"
        f"Validation return code: {validation.returncode}\n\n"
        f"Validation output:\n{validation.output}"
    )


def _require_aider_success(result: AiderResult, *, phase: str) -> None:
    if result.success:
        return
    detail = (result.stderr or result.stdout or "Aider produced no output")[-1500:]
    raise RuntimeError(f"Aider {phase} failed (code {result.returncode}): {detail}")


def _validation_failure_message(validation: ValidationResult, repair_attempts: int) -> str:
    return (
        "Local validation is still failing after "
        f"{repair_attempts} repair attempt(s); refusing to push or open a PR. "
        f"Command: {validation.command!r}. Output: {validation.output}"
    )


def _notify(task: dict[str, Any], text: str) -> None:
    try:
        publish_report_to_channel(text, channel_id=task["channel_id"], thread_ts=task.get("thread_ts"))
    except Exception:
        logger.exception("Failed to post builder result to Slack for task %s", task["task_id"])

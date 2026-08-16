"""Long-running Builder coding-harness worker."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from src.agents.builder.task_store import BuilderTaskStore
from src.bot.blockkit.builder import builder_status_blocks
from src.core.config import settings
from src.integrations.github_client import GitHubClientError, create_pull_request
from src.reporting.publisher import publish_report_to_channel
from src.worker.aider_runner import AiderResult, run_aider
from src.worker.validation import ValidationResult, run_validation
from src.worker.workspace import (
    WorkspaceError,
    Worktree,
    cleanup_worktree,
    commit_pending_changes,
    has_repository_changes,
    prepare_worktree,
    push_branch,
)

logger = logging.getLogger(__name__)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


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
    logger.info("Claimed builder turn %s", task_id)
    worktree: Worktree | None = None
    try:
        worktree = prepare_worktree(task_id)
        store.mark_running(task_id, branch_name=worktree.branch_name)
        _publish_status(
            store,
            task,
            status="running",
            summary="I’m inspecting the repository and working through your request on the device.",
            branch_name=worktree.branch_name,
        )

        initial = run_aider(goal=task["goal"], worktree_path=worktree.path)
        _require_aider_success(initial, phase="initial turn")

        # Natural harness behaviour: questions and inspections do not manufacture
        # a PR. If Aider made no repository change, return its answer as normal
        # conversation and finish the turn.
        if not has_repository_changes(worktree):
            answer = _aider_answer(initial)
            store.mark_succeeded(task_id, result_text=answer)
            _publish_status(
                store,
                task,
                status="answered",
                summary="I inspected the repository and didn’t need to change files for this turn.",
                branch_name=worktree.branch_name,
            )
            publish_report_to_channel(
                answer,
                channel_id=task["channel_id"],
                thread_ts=task.get("thread_ts"),
            )
            return

        validation, repair_attempts = _validate_and_repair(store, task, worktree)
        if not validation.success and settings.builder_require_tests_pass:
            raise RuntimeError(_validation_failure_message(validation, repair_attempts))

        # Aider normally commits automatically. This catches any legitimate
        # edits left dirty after a repair so the published branch matches what
        # was actually validated.
        commit_pending_changes(worktree, message=f"Builder Agent: {_latest_request(task['goal'])[:72]}")
        push_branch(worktree)
        validation_label = "passed" if validation.success else "not required"
        latest_request = _latest_request(task["goal"])
        pr = create_pull_request(
            branch_name=worktree.branch_name,
            title=f"Builder Agent: {latest_request[:72]}",
            body=(
                "Automated change requested through the natural Slack Builder harness.\n\n"
                f"Latest request:\n{latest_request}\n\n"
                f"Turn: `{task_id}`\n\n"
                "Local validation:\n"
                f"- Status: **{validation_label}**\n"
                f"- Command: `{validation.command or '(disabled)'}`\n"
                f"- Repair attempts: {repair_attempts}\n\n"
                f"Builder model checkpoint: `{settings.builder_model_checkpoint}`\n"
                f"Inference endpoint: `{settings.builder_llm_base_url}`"
            ),
        )
        store.mark_succeeded(task_id, pr_url=pr["html_url"])
        _publish_status(
            store,
            task,
            status="completed",
            summary="The repository change is locally green and has been published for review.",
            branch_name=worktree.branch_name,
            validation="✅ passed" if validation.success else "not required",
            repair_attempt=str(repair_attempts),
            pr_url=pr["html_url"],
        )

    except (WorkspaceError, GitHubClientError, RuntimeError) as exc:
        logger.exception("Builder turn %s failed", task_id)
        store.mark_failed(task_id, error_message=str(exc)[:2000])
        _publish_status(
            store,
            task,
            status="failed",
            summary=f"I stopped without publishing the change. {str(exc)[:1200]}",
            branch_name=worktree.branch_name if worktree else None,
        )
    except Exception as exc:
        logger.exception("Builder turn %s failed unexpectedly", task_id)
        store.mark_failed(task_id, error_message=str(exc)[:2000])
        _publish_status(
            store,
            task,
            status="failed",
            summary=f"I stopped unexpectedly without publishing. {str(exc)[:1200]}",
            branch_name=worktree.branch_name if worktree else None,
        )
    finally:
        if worktree is not None:
            cleanup_worktree(worktree)


def _validate_and_repair(
    store: BuilderTaskStore,
    task: dict[str, Any],
    worktree: Worktree,
) -> tuple[ValidationResult, int]:
    """Run local validation and let the coding model repair failures in-place."""
    validation = run_validation(worktree_path=worktree.path)
    repair_attempts = 0

    while not validation.success and repair_attempts < settings.builder_max_repair_attempts:
        repair_attempts += 1
        _publish_status(
            store,
            task,
            status="repairing",
            summary="The local gates found a problem, so I’m fixing it before anything is pushed.",
            branch_name=worktree.branch_name,
            validation="❌ failed",
            repair_attempt=f"{repair_attempts}/{settings.builder_max_repair_attempts}",
        )
        repair_goal = _repair_prompt(task["goal"], validation, repair_attempts)
        repair = run_aider(goal=repair_goal, worktree_path=worktree.path)
        _require_aider_success(repair, phase=f"repair attempt {repair_attempts}")
        validation = run_validation(worktree_path=worktree.path)

    if validation.success:
        _publish_status(
            store,
            task,
            status="validated",
            summary="The configured local repository gates are green.",
            branch_name=worktree.branch_name,
            validation="✅ passed",
            repair_attempt=str(repair_attempts),
        )
    return validation, repair_attempts


def _repair_prompt(goal: str, validation: ValidationResult, attempt: int) -> str:
    return (
        "The requested implementation is not yet ready to publish. Fix the code in this "
        "worktree so the required validation passes. Do not remove, skip, weaken, or xfail "
        "tests merely to make the suite green, and do not undo the original requested change.\n\n"
        f"Original conversational request:\n{goal}\n\n"
        f"Repair attempt: {attempt}\n"
        f"Validation command: {validation.command}\n"
        f"Validation return code: {validation.returncode}\n\n"
        f"Validation output:\n{validation.output}"
    )


def _latest_request(goal: str) -> str:
    marker = "Latest request:\n"
    if marker in goal:
        return goal.rsplit(marker, 1)[-1].strip()
    # Root turns have no prior Slack transcript. The Builder harness instruction
    # is separated from the request by a blank line, so the final paragraph is
    # the best human-readable PR summary.
    parts = [part.strip() for part in goal.split("\n\n") if part.strip()]
    return parts[-1] if parts else goal.strip()


def _aider_answer(result: AiderResult) -> str:
    """Return a compact conversational answer from Aider's captured output."""
    text = _ANSI_RE.sub("", (result.stdout or result.stderr or "").strip())
    if not text:
        return "I inspected the repository and didn’t find anything else I needed to change."
    # Keep Slack conversational; very long CLI transcripts are not useful chat.
    return text[-8000:]


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


def _publish_status(
    store: BuilderTaskStore,
    task: dict[str, Any],
    *,
    status: str,
    summary: str,
    branch_name: str | None = None,
    validation: str | None = None,
    repair_attempt: str | None = None,
    pr_url: str | None = None,
) -> None:
    """Create or update one persistent Block Kit card for this Builder turn."""
    try:
        latest = store.get(task["task_id"]) or task
        message_ts = latest.get("progress_message_ts")
        fallback = f"[Builder status] {task['task_id']}: {summary}"
        result = publish_report_to_channel(
            fallback,
            channel_id=task["channel_id"],
            thread_ts=task.get("thread_ts"),
            blocks=builder_status_blocks(
                task_id=task["task_id"],
                status=status,
                summary=summary,
                branch_name=branch_name,
                validation=validation,
                repair_attempt=repair_attempt,
                pr_url=pr_url,
            ),
            update_message_ts=message_ts,
        )
        if not message_ts and result.get("ts"):
            store.set_progress_message_ts(task["task_id"], result["ts"])
    except Exception:
        logger.exception("Failed to publish Builder status for %s", task["task_id"])

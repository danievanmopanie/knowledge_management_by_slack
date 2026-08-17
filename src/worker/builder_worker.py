"""Long-running Builder coding-harness worker."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, TypeVar

from src.agents.builder.task_store import BuilderTaskStore
from src.bot.blockkit.builder import builder_status_blocks
from src.core.config import settings
from src.integrations.github_client import (
    GitHubClientError,
    create_pull_request,
    get_pull_request,
    pull_request_is_open,
)
from src.reporting.publisher import publish_report_to_channel
from src.ux.background_activity import ActivitySnapshot, BackgroundActivity
from src.worker.aider_runner import AiderResult, run_aider
from src.worker.terminal_harness import run_terminal_harness
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
_T = TypeVar("_T")


def run_forever() -> None:
    store = BuilderTaskStore()
    logger.info("Builder worker started, polling every %ss", settings.builder_poll_interval_seconds)
    while True:
        task = store.claim_next()
        if task is None:
            time.sleep(settings.builder_poll_interval_seconds)
            continue
        _run_task(store, task)


def _open_continuation(task: dict[str, Any]) -> tuple[str | None, str | None]:
    branch = task.get("continuation_branch")
    pr_url = task.get("continuation_pr_url")
    if not branch or not pr_url:
        return None, None
    try:
        if pull_request_is_open(pr_url):
            return branch, pr_url
    except GitHubClientError:
        logger.exception("Could not verify continuation PR for task %s", task["task_id"])
    return None, None


def _resolve_target(task: dict[str, Any]) -> tuple[str | None, str | None, dict[str, Any] | None]:
    if task.get("handoff_pr_number"):
        pr = get_pull_request(task["handoff_pr_number"])
        return pr["head_ref"], pr["html_url"], pr
    branch, pr_url = _open_continuation(task)
    return branch, pr_url, None


def _goal_with_pr_context(goal: str, pr: dict[str, Any] | None) -> str:
    if not pr:
        return goal
    body = str(pr.get("body") or "").strip()
    if len(body) > 8000:
        body = body[:8000] + "\n[PR body truncated]"
    return (
        f"{goal}\n\n"
        "Existing GitHub pull request handoff:\n"
        f"PR: #{pr['number']}\n"
        f"URL: {pr['html_url']}\n"
        f"Title: {pr['title']}\n"
        f"Head branch: {pr['head_ref']}\n"
        f"Base branch: {pr['base_ref']}\n"
        f"PR body:\n{body or '(empty)'}\n\n"
        "You are already checked out on this PR's head branch on the GX10. "
        "Inspect the actual branch and execute meaningful local checks. Repair this same branch "
        "when evidence shows a problem; do not create a replacement PR."
    )


def _execute_primary_harness(*, goal: str, worktree: Worktree) -> str:
    if settings.builder_terminal_enabled:
        result = run_terminal_harness(goal=goal, worktree_path=worktree.path)
        if not result.success:
            raise RuntimeError(result.error or "Builder terminal harness failed.")
        logger.info(
            "Builder terminal harness completed task=%s tool_calls=%s",
            worktree.task_id,
            result.tool_calls,
        )
        return result.answer

    initial = run_aider(goal=goal, worktree_path=worktree.path)
    _require_aider_success(initial, phase="initial turn")
    return _aider_answer(initial)


def _run_with_heartbeat(
    store: BuilderTaskStore,
    task: dict[str, Any],
    worktree: Worktree,
    *,
    status: str,
    summary: str,
    operation: Callable[[], _T],
    validation: str | None = None,
    repair_attempt: str | None = None,
    pr_url: str | None = None,
) -> _T:
    """Run a blocking phase while keeping one Slack progress card visibly alive."""

    def publish(snapshot: ActivitySnapshot) -> None:
        _publish_status(
            store,
            task,
            status=status,
            summary=snapshot.summary,
            branch_name=worktree.branch_name,
            validation=validation,
            repair_attempt=repair_attempt,
            pr_url=pr_url,
            elapsed_seconds=snapshot.elapsed_seconds,
            idle_seconds=snapshot.idle_seconds,
            heartbeat=snapshot.heartbeat,
        )

    activity = BackgroundActivity(
        publish,
        heartbeat_seconds=settings.background_heartbeat_seconds,
    )
    activity.start(status, summary)
    try:
        return operation()
    finally:
        activity.stop()


def _run_task(store: BuilderTaskStore, task: dict[str, Any]) -> None:
    task_id = task["task_id"]
    logger.info("Claimed builder turn %s", task_id)
    worktree: Worktree | None = None
    target_branch: str | None = None
    target_pr_url: str | None = None
    handoff_pr: dict[str, Any] | None = None
    try:
        target_branch, target_pr_url, handoff_pr = _resolve_target(task)
        explicit_handoff = handoff_pr is not None
        worktree = prepare_worktree(task_id, continuation_branch=target_branch)
        store.mark_running(task_id, branch_name=worktree.branch_name)

        goal = _goal_with_pr_context(task["goal"], handoff_pr)
        answer = _run_with_heartbeat(
            store,
            task,
            worktree,
            status="running",
            summary=(
                f"I’ve checked out PR #{handoff_pr['number']} on the GX10 and I’m inspecting and executing it locally."
                if explicit_handoff
                else (
                    "I’m continuing the open pull request from this Slack thread and executing your latest request on the GX10."
                    if target_pr_url
                    else "I’m inspecting the repository and executing your request on the GX10."
                )
            ),
            operation=lambda: _execute_primary_harness(goal=goal, worktree=worktree),
            pr_url=target_pr_url,
        )
        changed = has_repository_changes(worktree)

        if explicit_handoff:
            validation_result, repair_attempts = _validate_and_repair(store, task, worktree)
            if not validation_result.success and settings.builder_require_tests_pass:
                raise RuntimeError(_validation_failure_message(validation_result, repair_attempts))

            changed = has_repository_changes(worktree)
            if changed:
                _publish_status(
                    store,
                    task,
                    status="running",
                    summary="The code is green. I’m committing and pushing the proven repair back to the handed-off PR.",
                    branch_name=worktree.branch_name,
                    validation="✅ passed",
                    pr_url=target_pr_url,
                )
                latest_request = _latest_request(task["goal"])
                commit_pending_changes(worktree, message=f"Builder Agent: {latest_request[:72]}")
                push_branch(worktree)

            store.mark_succeeded(task_id, pr_url=target_pr_url, result_text=answer)
            _publish_status(
                store,
                task,
                status="completed",
                summary=(
                    "The handed-off PR is green on the GX10 and any repairs were pushed back to the same pull request."
                    if changed
                    else "The handed-off PR is green on the GX10; no repair commit was required."
                ),
                branch_name=worktree.branch_name,
                validation="✅ passed" if validation_result.success else "not required",
                repair_attempt=str(repair_attempts),
                pr_url=target_pr_url,
            )
            if answer:
                publish_report_to_channel(answer, channel_id=task["channel_id"], thread_ts=task.get("thread_ts"))
            return

        if not changed:
            store.mark_succeeded(task_id, pr_url=target_pr_url, result_text=answer)
            _publish_status(
                store,
                task,
                status="answered",
                summary="I inspected/executed on the GX10 and didn’t need to change files for this turn.",
                branch_name=worktree.branch_name,
                pr_url=target_pr_url,
            )
            publish_report_to_channel(answer, channel_id=task["channel_id"], thread_ts=task.get("thread_ts"))
            return

        validation_result, repair_attempts = _validate_and_repair(store, task, worktree)
        if not validation_result.success and settings.builder_require_tests_pass:
            raise RuntimeError(_validation_failure_message(validation_result, repair_attempts))

        _publish_status(
            store,
            task,
            status="running",
            summary="The local gates are green. I’m committing and publishing the change now.",
            branch_name=worktree.branch_name,
            validation="✅ passed",
            repair_attempt=str(repair_attempts),
            pr_url=target_pr_url,
        )
        latest_request = _latest_request(task["goal"])
        commit_pending_changes(worktree, message=f"Builder Agent: {latest_request[:72]}")
        push_branch(worktree)
        validation_label = "passed" if validation_result.success else "not required"

        if target_pr_url:
            pr_url = target_pr_url
            completion_summary = "Your follow-up is locally green and has been pushed to the existing pull request."
        else:
            pr = create_pull_request(
                branch_name=worktree.branch_name,
                title=f"Builder Agent: {latest_request[:72]}",
                body=(
                    "Automated change requested through the natural Slack Builder harness.\n\n"
                    f"Latest request:\n{latest_request}\n\n"
                    f"Turn: `{task_id}`\n\n"
                    "Local validation:\n"
                    f"- Status: **{validation_label}**\n"
                    f"- Command: `{validation_result.command or '(disabled)'}`\n"
                    f"- Repair attempts: {repair_attempts}\n\n"
                    f"Builder model checkpoint: `{settings.builder_model_checkpoint}`\n"
                    f"Inference endpoint: `{settings.builder_llm_base_url}`"
                ),
            )
            pr_url = pr["html_url"]
            completion_summary = "The repository change is locally green and has been published for review."

        store.mark_succeeded(task_id, pr_url=pr_url, result_text=answer)
        _publish_status(
            store,
            task,
            status="completed",
            summary=completion_summary,
            branch_name=worktree.branch_name,
            validation="✅ passed" if validation_result.success else "not required",
            repair_attempt=str(repair_attempts),
            pr_url=pr_url,
        )
        if answer:
            publish_report_to_channel(answer, channel_id=task["channel_id"], thread_ts=task.get("thread_ts"))

    except (WorkspaceError, GitHubClientError, RuntimeError) as exc:
        logger.exception("Builder turn %s failed", task_id)
        store.mark_failed(task_id, error_message=str(exc)[:2000])
        _publish_status(
            store,
            task,
            status="failed",
            summary=f"I stopped without publishing the change. {str(exc)[:1200]}",
            branch_name=worktree.branch_name if worktree else target_branch,
            pr_url=target_pr_url,
        )
    except Exception as exc:
        logger.exception("Builder turn %s failed unexpectedly", task_id)
        store.mark_failed(task_id, error_message=str(exc)[:2000])
        _publish_status(
            store,
            task,
            status="failed",
            summary=f"I stopped unexpectedly without publishing. {str(exc)[:1200]}",
            branch_name=worktree.branch_name if worktree else target_branch,
            pr_url=target_pr_url,
        )
    finally:
        if worktree is not None:
            cleanup_worktree(worktree)


def _validate_and_repair(
    store: BuilderTaskStore,
    task: dict[str, Any],
    worktree: Worktree,
) -> tuple[ValidationResult, int]:
    validation = _run_with_heartbeat(
        store,
        task,
        worktree,
        status="running",
        summary="I’m running the configured local validation gates. This can take a few minutes.",
        operation=lambda: run_validation(worktree_path=worktree.path),
        validation="⏳ running",
        pr_url=task.get("continuation_pr_url"),
    )
    repair_attempts = 0

    while not validation.success and repair_attempts < settings.builder_max_repair_attempts:
        repair_attempts += 1
        repair_goal = _repair_prompt(task["goal"], validation, repair_attempts)
        repair = _run_with_heartbeat(
            store,
            task,
            worktree,
            status="repairing",
            summary="The local gates found a problem. I’m repairing it before anything is pushed.",
            operation=lambda: run_aider(goal=repair_goal, worktree_path=worktree.path),
            validation="❌ failed",
            repair_attempt=f"{repair_attempts}/{settings.builder_max_repair_attempts}",
            pr_url=task.get("continuation_pr_url"),
        )
        _require_aider_success(repair, phase=f"repair attempt {repair_attempts}")
        validation = _run_with_heartbeat(
            store,
            task,
            worktree,
            status="running",
            summary="The repair is applied. I’m rerunning the local validation gates.",
            operation=lambda: run_validation(worktree_path=worktree.path),
            validation="⏳ rerunning",
            repair_attempt=f"{repair_attempts}/{settings.builder_max_repair_attempts}",
            pr_url=task.get("continuation_pr_url"),
        )

    if validation.success:
        _publish_status(
            store,
            task,
            status="validated",
            summary="The configured local repository gates are green.",
            branch_name=worktree.branch_name,
            validation="✅ passed",
            repair_attempt=str(repair_attempts),
            pr_url=task.get("continuation_pr_url"),
        )
    return validation, repair_attempts


def _repair_prompt(goal: str, validation: ValidationResult, attempt: int) -> str:
    return (
        "The requested implementation is not yet ready to publish. Fix the code in this worktree so the required validation passes. "
        "Do not remove, skip, weaken, or xfail tests merely to make the suite green, and do not undo the original requested change.\n\n"
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
    parts = [part.strip() for part in goal.split("\n\n") if part.strip()]
    return parts[-1] if parts else goal.strip()


def _aider_answer(result: AiderResult) -> str:
    text = _ANSI_RE.sub("", (result.stdout or result.stderr or "").strip())
    if not text:
        return "I inspected the repository and didn’t find anything else I needed to change."
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
    elapsed_seconds: int | None = None,
    idle_seconds: int | None = None,
    heartbeat: bool = False,
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
                elapsed_seconds=elapsed_seconds,
                idle_seconds=idle_seconds,
                heartbeat=heartbeat,
            ),
            update_message_ts=message_ts,
        )
        if not message_ts and result.get("ts"):
            store.set_progress_message_ts(task["task_id"], result["ts"])
    except Exception:
        logger.exception("Failed to publish Builder status for %s", task["task_id"])

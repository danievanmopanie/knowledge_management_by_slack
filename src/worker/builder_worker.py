"""Long-running Builder coding-harness worker."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
from src.worker.aider_runner import AiderResult, run_aider
from src.worker.secret_exposure_check import check_secret_exposure, log_and_warn_findings
from src.worker.terminal_harness import HarnessStepEvent, run_terminal_harness
from src.worker.turn_control import TurnControl, parse_deadline
from src.worker.validation import ValidationResult, run_validation
from src.worker.workspace import (
    WorkspaceError,
    Worktree,
    cleanup_worktree,
    commit_pending_changes,
    has_repository_changes,
    prepare_worktree,
    prune_orphaned_worktrees,
    push_branch,
)

logger = logging.getLogger(__name__)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def run_forever() -> None:
    store = BuilderTaskStore()
    if settings.builder_secret_check_enabled:
        log_and_warn_findings(check_secret_exposure())
    if settings.builder_startup_recovery_enabled:
        _recover_on_startup(store)
    logger.info("Builder worker started, polling every %ss", settings.builder_poll_interval_seconds)
    while True:
        task = store.claim_next()
        if task is None:
            time.sleep(settings.builder_poll_interval_seconds)
            continue
        _run_task(store, task)


def _recover_on_startup(store: BuilderTaskStore) -> None:
    """Fail and surface any turn still 'running' from a crashed worker process."""
    prune_orphaned_worktrees()
    for task in store.recover_stuck_tasks():
        logger.warning("Recovered stuck Builder turn %s as failed", task["task_id"])
        branch_name = task.get("branch_name")
        if branch_name:
            cleanup_worktree(
                Worktree(
                    task_id=task["task_id"],
                    branch_name=branch_name,
                    path=Path(settings.builder_workdir) / task["task_id"],
                )
            )
        _publish_status(
            store,
            task,
            status="failed",
            summary=(
                "The Builder worker restarted while this turn was in progress, so it could not "
                "be safely resumed automatically. If a pull request already exists for this "
                "branch, check GitHub directly; otherwise, resend your request."
            ),
            branch_name=branch_name,
            pr_url=task.get("pr_url"),
        )


def _open_continuation(task: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return the branch/PR to continue when the previous thread PR is open."""
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


def _resolve_target(
    task: dict[str, Any],
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    """Resolve explicit external PR handoff first, then implicit thread continuation."""
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


class _ProgressThrottle:
    """Bounds how often the persistent Slack card is updated mid-turn.

    Slack has no published exact rate limit for `chat.update`, but bursty
    per-second updates are known to trigger 429s in practice. A missed tick is
    non-fatal here — `_publish_status` already swallows/logs errors, and the
    next throttled tick recovers automatically.
    """

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self._last >= self.min_interval:
            self._last = now
            return True
        return False


def _make_progress_reporter(
    *,
    store: BuilderTaskStore,
    task: dict[str, Any],
    worktree: Worktree,
    pr_url: str | None,
    running_summary: str,
) -> Callable[[HarnessStepEvent], None]:
    """Build a throttled on_step callback that updates the same persistent card."""
    throttle = _ProgressThrottle(settings.builder_progress_min_interval_seconds)

    def on_step(event: HarnessStepEvent) -> None:
        if not throttle.ready():
            return
        current_step = f"{event.tool_name}: {event.detail}" if event.detail else event.tool_name
        _publish_status(
            store,
            task,
            status="running",
            summary=running_summary,
            branch_name=worktree.branch_name,
            pr_url=pr_url,
            current_step=current_step,
            show_cancel=True,
        )

    return on_step


def _execute_primary_harness(
    *,
    goal: str,
    worktree: Worktree,
    control: TurnControl | None = None,
    on_step: Callable[[HarnessStepEvent], None] | None = None,
) -> tuple[str, str | None]:
    """Run the real terminal tool loop, falling back to legacy Aider only if disabled.

    Returns (answer, stopped_reason). A non-None stopped_reason means the turn
    was cooperatively cancelled or hit its overall deadline — expected control
    flow, not an error, and the caller must not raise on it.
    """
    if settings.builder_terminal_enabled:
        result = run_terminal_harness(
            goal=goal, worktree_path=worktree.path, control=control, on_step=on_step
        )
        if result.stopped_reason:
            return "", result.stopped_reason
        if not result.success:
            raise RuntimeError(result.error or "Builder terminal harness failed.")
        logger.info(
            "Builder terminal harness completed task=%s tool_calls=%s",
            worktree.task_id,
            result.tool_calls,
        )
        return result.answer, None

    initial = run_aider(goal=goal, worktree_path=worktree.path)
    _require_aider_success(initial, phase="initial turn")
    return _aider_answer(initial), None


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
        control = TurnControl(
            store=store, task_id=task_id, deadline_at=parse_deadline(task.get("deadline_at"))
        )
        running_summary = (
            f"I’ve checked out PR #{handoff_pr['number']} on the GX10 and I’m executing it locally."
            if explicit_handoff
            else (
                "I’m continuing the open pull request from this Slack thread on the GX10."
                if target_pr_url
                else "I’m inspecting the repository and executing your request on the GX10."
            )
        )
        _publish_status(
            store,
            task,
            status="running",
            summary=running_summary,
            branch_name=worktree.branch_name,
            pr_url=target_pr_url,
            show_cancel=True,
        )

        goal = _goal_with_pr_context(task["goal"], handoff_pr)
        on_step = _make_progress_reporter(
            store=store, task=task, worktree=worktree, pr_url=target_pr_url, running_summary=running_summary
        )
        answer, stopped_reason = _execute_primary_harness(
            goal=goal, worktree=worktree, control=control, on_step=on_step
        )
        if stopped_reason:
            _finish_stopped_turn(
                store, task, stopped_reason=stopped_reason,
                branch_name=worktree.branch_name, pr_url=target_pr_url,
            )
            return
        changed = has_repository_changes(worktree)

        # Explicit PR handoff is a proof request, not merely a conversational
        # inspection. Always run the deterministic repository gate on the GX10,
        # even when the PR needed no edits during the model-driven tool loop.
        if explicit_handoff:
            validation, repair_attempts, stopped_reason = _validate_and_repair(
                store, task, worktree, control
            )
            if stopped_reason:
                _finish_stopped_turn(
                    store, task, stopped_reason=stopped_reason,
                    branch_name=worktree.branch_name, pr_url=target_pr_url,
                )
                return
            if not validation.success and settings.builder_require_tests_pass:
                raise RuntimeError(_validation_failure_message(validation, repair_attempts))

            changed = has_repository_changes(worktree)
            if changed:
                latest_request = _latest_request(task["goal"])
                commit_pending_changes(
                    worktree,
                    message=f"Builder Agent: {latest_request[:72]}",
                )
                push_branch(worktree)

            store.mark_succeeded(task_id, pr_url=target_pr_url, result_text=answer)
            _publish_status(
                store,
                task,
                status="completed",
                summary=(
                    "The handed-off PR is green on the GX10 and any repairs were pushed back "
                    "to the same pull request."
                    if changed
                    else "The handed-off PR is green on the GX10; no repair commit was required."
                ),
                branch_name=worktree.branch_name,
                validation="✅ passed" if validation.success else "not required",
                repair_attempt=str(repair_attempts),
                pr_url=target_pr_url,
                show_merge_deploy=_show_merge_deploy(target_pr_url, validation),
            )
            if answer:
                publish_report_to_channel(
                    answer,
                    channel_id=task["channel_id"],
                    thread_ts=task.get("thread_ts"),
                )
            return

        # Natural harness behaviour for ordinary conversation: questions and
        # runtime inspections do not manufacture a PR when no files changed.
        if not changed:
            store.mark_succeeded(
                task_id,
                pr_url=target_pr_url,
                result_text=answer,
            )
            _publish_status(
                store,
                task,
                status="answered",
                summary="I inspected/executed on the GX10 and didn’t need to change files for this turn.",
                branch_name=worktree.branch_name,
                pr_url=target_pr_url,
            )
            publish_report_to_channel(
                answer,
                channel_id=task["channel_id"],
                thread_ts=task.get("thread_ts"),
            )
            return

        validation, repair_attempts, stopped_reason = _validate_and_repair(store, task, worktree, control)
        if stopped_reason:
            _finish_stopped_turn(
                store, task, stopped_reason=stopped_reason,
                branch_name=worktree.branch_name, pr_url=target_pr_url,
            )
            return
        if not validation.success and settings.builder_require_tests_pass:
            raise RuntimeError(_validation_failure_message(validation, repair_attempts))

        latest_request = _latest_request(task["goal"])
        commit_pending_changes(worktree, message=f"Builder Agent: {latest_request[:72]}")
        push_branch(worktree)
        validation_label = "passed" if validation.success else "not required"

        if target_pr_url:
            pr_url = target_pr_url
            completion_summary = (
                "Your follow-up is locally green and has been pushed to the existing pull request."
            )
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
                    f"- Command: `{validation.command or '(disabled)'}`\n"
                    f"- Repair attempts: {repair_attempts}\n\n"
                    f"Builder model checkpoint: `{settings.builder_model_checkpoint}`\n"
                    f"Inference endpoint: `{settings.builder_llm_base_url}`"
                ),
            )
            pr_url = pr["html_url"]
            completion_summary = (
                "The repository change is locally green and has been published for review."
            )

        store.mark_succeeded(task_id, pr_url=pr_url, result_text=answer)
        _publish_status(
            store,
            task,
            status="completed",
            summary=completion_summary,
            branch_name=worktree.branch_name,
            validation="✅ passed" if validation.success else "not required",
            repair_attempt=str(repair_attempts),
            pr_url=pr_url,
            show_merge_deploy=_show_merge_deploy(pr_url, validation),
        )
        if answer:
            publish_report_to_channel(
                answer,
                channel_id=task["channel_id"],
                thread_ts=task.get("thread_ts"),
            )

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
    control: TurnControl | None = None,
) -> tuple[ValidationResult, int, str | None]:
    """Run local validation and let the coding model repair failures in-place.

    The third return value is a stop reason ("cancelled"/"deadline") when the
    caller stopped before another repair attempt started — checked once per
    loop iteration, not mid-attempt, so a repair already underway completes.
    """
    validation = run_validation(worktree_path=worktree.path)
    repair_attempts = 0

    while not validation.success and repair_attempts < settings.builder_max_repair_attempts:
        if control and (reason := control.check()):
            return validation, repair_attempts, reason
        repair_attempts += 1
        _publish_status(
            store,
            task,
            status="repairing",
            summary="The local gates found a problem, so I’m fixing it before anything is pushed.",
            branch_name=worktree.branch_name,
            validation="❌ failed",
            repair_attempt=f"{repair_attempts}/{settings.builder_max_repair_attempts}",
            pr_url=task.get("continuation_pr_url"),
            show_cancel=True,
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
            pr_url=task.get("continuation_pr_url"),
        )
    return validation, repair_attempts, None


def _finish_stopped_turn(
    store: BuilderTaskStore,
    task: dict[str, Any],
    *,
    stopped_reason: str,
    branch_name: str | None,
    pr_url: str | None,
) -> None:
    """Handle a turn that stopped cooperatively (cancel or deadline), not an error."""
    task_id = task["task_id"]
    if stopped_reason == "cancelled":
        store.mark_cancelled_from_running(task_id)
        _publish_status(
            store,
            task,
            status="cancelled",
            summary=(
                "Stopped because a Cancel was requested from Slack before this turn finished. "
                "No validation ran and nothing was pushed."
            ),
            branch_name=branch_name,
            pr_url=pr_url,
        )
        return

    store.mark_failed(task_id, error_message="Exceeded the configured overall turn deadline.")
    _publish_status(
        store,
        task,
        status="timed_out",
        summary=(
            f"Stopped after exceeding the configured {settings.builder_turn_deadline_seconds}s "
            "overall deadline for one Slack turn. Nothing was pushed. Consider breaking the "
            "request into smaller turns."
        ),
        branch_name=branch_name,
        pr_url=pr_url,
    )


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
    parts = [part.strip() for part in goal.split("\n\n") if part.strip()]
    return parts[-1] if parts else goal.strip()


def _aider_answer(result: AiderResult) -> str:
    """Return a compact conversational answer from Aider's captured output."""
    text = _ANSI_RE.sub("", (result.stdout or result.stderr or "").strip())
    if not text:
        return "I inspected the repository and didn’t find anything else I needed to change."
    return text[-8000:]


def _require_aider_success(result: AiderResult, *, phase: str) -> None:
    if result.success:
        return
    detail = (result.stderr or result.stdout or "Aider produced no output")[-1500:]
    raise RuntimeError(f"Aider {phase} failed (code {result.returncode}): {detail}")


def _show_merge_deploy(pr_url: str | None, validation: ValidationResult) -> bool:
    """The Merge & Deploy button only ever appears when there's something to
    merge, it's actually green, and the operator has explicitly configured
    which unit(s) it's allowed to restart. An unconfigured deployment target
    keeps the button hidden by default rather than failing loudly."""
    return bool(pr_url) and validation.success and bool(settings.builder_deploy_restart_units.strip())


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
    current_step: str | None = None,
    show_cancel: bool = False,
    show_merge_deploy: bool = False,
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
                current_step=current_step,
                show_cancel=show_cancel,
                show_merge_deploy=show_merge_deploy,
            ),
            update_message_ts=message_ts,
        )
        if not message_ts and result.get("ts"):
            store.set_progress_message_ts(task["task_id"], result["ts"])
    except Exception:
        logger.exception("Failed to publish Builder status for %s", task["task_id"])

"""Long-running poll loop: claims queued Builder Agent tasks and executes them."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.agents.builder.task_store import BuilderTaskStore
from src.core.config import settings
from src.integrations.github_client import GitHubClientError, create_pull_request
from src.reporting.publisher import publish_report_to_channel
from src.worker.aider_runner import run_aider
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

        result = run_aider(goal=task["goal"], worktree_path=worktree.path)
        if not result.success:
            raise RuntimeError(f"Aider failed (code {result.returncode}): {result.stderr[-1500:]}")

        push_branch(worktree)
        pr = create_pull_request(
            branch_name=worktree.branch_name,
            title=f"Builder Agent: {task['goal'][:72]}",
            body=f"Automated change requested via Slack Builder Agent.\n\nGoal:\n{task['goal']}\n\nTask: `{task_id}`",
        )
        store.mark_succeeded(task_id, pr_url=pr["html_url"])
        _notify(task, f"✅ Build task `{task_id}` complete: {pr['html_url']}")

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


def _notify(task: dict[str, Any], text: str) -> None:
    try:
        publish_report_to_channel(text, channel_id=task["channel_id"], thread_ts=task.get("thread_ts"))
    except Exception:
        logger.exception("Failed to post builder result to Slack for task %s", task["task_id"])

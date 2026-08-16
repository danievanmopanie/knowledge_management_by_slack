"""Slack interactivity for the Builder Agent's persistent status card.

Ownership invariant: exactly one process updates a given card's `chat.update`
at a time. The worker (`src/worker/builder_worker.py`) owns it through
running -> repairing/validated -> completed/failed/cancelled/timed_out. The
Cancel handler below never calls `chat_update` — it only requests a
cooperative stop (via `cancel_requested` in SQLite) and gives the clicking
user ephemeral feedback; the actual card transition to `cancelled` happens
later, from the worker, once it notices the flag between steps.

The Merge & Deploy handler is the one exception: by the time that button is
visible, the card has already reached a terminal `completed` state and the
worker has finished with that task_id for good, so ownership of the card has
cleanly transferred to this handler. It is the only place in this module that
calls `chat_update`.
"""

from __future__ import annotations

import asyncio
import logging

from slack_bolt.async_app import AsyncApp

from src.agents.builder.agent import cancel_builder_task, is_builder_allowed
from src.agents.builder.deployment_store import BuilderDeploymentStore
from src.agents.builder.task_store import BuilderTaskStore
from src.bot.blockkit import ids
from src.bot.blockkit.builder import builder_status_blocks
from src.integrations.github_client import pr_number_from_url
from src.worker.deploy import merge_and_deploy

logger = logging.getLogger(__name__)


def register(app: AsyncApp) -> None:
    """Register all Builder Agent Block Kit interactivity handlers."""

    @app.action(ids.BUILDER_CANCEL_TURN)
    async def cancel_builder_turn(ack, body, client):
        await ack()
        user_id = (body.get("user") or {}).get("id")
        channel_id = (body.get("channel") or {}).get("id")
        if not is_builder_allowed(user_id):
            if channel_id and user_id:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="You're not on the Builder Agent allowlist for this action.",
                )
            return

        task_id = body["actions"][0]["value"]
        try:
            result_text = cancel_builder_task(BuilderTaskStore(), task_id)
        except Exception:
            logger.exception("Builder cancel-button handling failed for task %s", task_id)
            result_text = "Something went wrong trying to cancel that turn. Try `cancel <task-id>` instead."

        if channel_id and user_id:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=result_text)

    @app.action(ids.BUILDER_MERGE_DEPLOY)
    async def merge_deploy_builder_turn(ack, body, client):
        # Slack requires ack() within 3 seconds regardless of how long the
        # merge + restart + health-check work below takes, so that work is
        # dispatched to a background task rather than awaited inline here.
        await ack()
        user_id = (body.get("user") or {}).get("id")
        channel_id = (body.get("channel") or {}).get("id")
        message_ts = (body.get("message") or {}).get("ts")
        if not is_builder_allowed(user_id):
            if channel_id and user_id:
                await client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text="You're not on the Builder Agent allowlist for this action.",
                )
            return

        task_id = body["actions"][0]["value"]
        task = BuilderTaskStore().get(task_id)
        if not task or not task.get("pr_url"):
            if channel_id and user_id:
                await client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text="No pull request found for this turn."
                )
            return

        asyncio.create_task(
            _run_merge_and_deploy(
                client,
                task=task,
                user_id=user_id or "",
                channel_id=channel_id or "",
                message_ts=message_ts,
            )
        )


async def _run_merge_and_deploy(client, *, task, user_id: str, channel_id: str, message_ts: str | None) -> None:
    task_id = task["task_id"]
    pr_url = task["pr_url"]
    pr_number = pr_number_from_url(pr_url)
    deployment_store = BuilderDeploymentStore()
    deployment_id = deployment_store.record(
        task_id=task_id,
        pr_number=pr_number or "",
        pr_url=pr_url,
        triggered_by=user_id,
        channel_id=channel_id,
        message_ts=message_ts,
    )

    if not pr_number:
        message = "Could not parse a pull request number from the stored PR URL."
        deployment_store.mark_result(
            deployment_id, success=False, merge_sha=None, restarted=[], error_message=message
        )
        await _update_card(client, channel_id, message_ts, task_id=task_id, status="deploy_failed", summary=message, pr_url=pr_url)
        return

    try:
        # merge_and_deploy makes blocking HTTP and subprocess calls; run it off
        # the event loop so other concurrent Slack interactions aren't stalled.
        result = await asyncio.to_thread(merge_and_deploy, pr_number=pr_number, pr_url=pr_url)
    except Exception as exc:
        logger.exception("Merge & Deploy failed for task %s", task_id)
        deployment_store.mark_result(
            deployment_id, success=False, merge_sha=None, restarted=[], error_message=str(exc)[:2000]
        )
        await _update_card(
            client, channel_id, message_ts, task_id=task_id, status="deploy_failed",
            summary=f"Merge & Deploy failed unexpectedly: {exc}"[:2900], pr_url=pr_url,
        )
        return

    deployment_store.mark_result(
        deployment_id,
        success=result.success,
        merge_sha=result.merge_sha,
        restarted=result.restarted,
        error_message=None if result.success else result.message,
    )
    await _update_card(
        client,
        channel_id,
        message_ts,
        task_id=task_id,
        status="deployed" if result.success else "deploy_failed",
        summary=result.message,
        pr_url=pr_url,
    )


async def _update_card(
    client, channel_id: str, message_ts: str | None, *, task_id: str, status: str, summary: str, pr_url: str
) -> None:
    if not channel_id or not message_ts:
        logger.warning("No channel/message_ts to update Builder card for task %s", task_id)
        return
    try:
        await client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=f"[Builder status] {task_id}: {summary}",
            blocks=builder_status_blocks(task_id=task_id, status=status, summary=summary, pr_url=pr_url),
        )
    except Exception:
        logger.exception("Failed to update Builder card for task %s after Merge & Deploy", task_id)

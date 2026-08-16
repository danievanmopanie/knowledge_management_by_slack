"""Dedicated Socket Mode runtime for the Create Knowledge agent."""

from __future__ import annotations

import asyncio
import logging
import os
import re

from dotenv import load_dotenv

# This standalone runtime reads dedicated Slack credentials directly from the
# process environment. Load .env before those reads so local CLI/systemd runs
# behave consistently with the shared Pydantic Settings object.
load_dotenv()

# The shared Settings object and Slack file downloader use SLACK_* variables.
# Map the dedicated app credentials before importing application modules so this
# process is fully isolated from Frontend Support and the legacy multi-agent app.
_create_bot = os.getenv("CREATE_KNOWLEDGE_SLACK_BOT_TOKEN", "").strip()
_create_app = os.getenv("CREATE_KNOWLEDGE_SLACK_APP_TOKEN", "").strip()
if _create_bot:
    os.environ["SLACK_BOT_TOKEN"] = _create_bot
if _create_app:
    os.environ["SLACK_APP_TOKEN"] = _create_app

from slack_bolt.async_app import AsyncApp  # noqa: E402
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler  # noqa: E402

from src.agents.knowledge_ingest import KnowledgeIngestAgent  # noqa: E402
from src.bot.blockkit import ids  # noqa: E402
from src.bot.create_knowledge_candidates import (  # noqa: E402
    candidate_outbox_loop,
    register_candidate_workflow,
)
from src.bot.create_knowledge_progress import build_blocks, queued_blocks  # noqa: E402
from src.bot.create_knowledge_staged import staged_incident_blocks, staged_upload_blocks  # noqa: E402
from src.core.config import settings  # noqa: E402
from src.core.context import RequestContext  # noqa: E402
from src.core.errors import safe_error_message  # noqa: E402
from src.knowledge.incident_ingest_jobs import IncidentIngestJobStore  # noqa: E402
from src.knowledge.knowledge_candidates import KnowledgeCandidateStore  # noqa: E402

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("CREATE_KNOWLEDGE_SLACK_BOT_TOKEN", "").strip()
APP_TOKEN = os.getenv("CREATE_KNOWLEDGE_SLACK_APP_TOKEN", "").strip()
app = AsyncApp(token=BOT_TOKEN or "xoxb-not-configured")
agent = KnowledgeIngestAgent()
job_store = IncidentIngestJobStore()
candidate_store = KnowledgeCandidateStore()
register_candidate_workflow(app, candidate_store)
_JOB_ID_RE = re.compile(r"\bING-[A-Z0-9]+\b", re.I)


def _context(event: dict) -> RequestContext:
    return RequestContext.from_slack(
        channel_id=event.get("channel", ""),
        user_id=event.get("user"),
        thread_ts=event.get("thread_ts") or event.get("ts"),
        files=event.get("files", []),
    )


def _action_context(body: dict) -> RequestContext:
    message = body.get("message") or {}
    container = body.get("container") or {}
    channel_id = (body.get("channel") or {}).get("id") or container.get("channel_id") or ""
    message_ts = message.get("ts") or container.get("message_ts")
    return RequestContext.from_slack(
        channel_id=channel_id,
        user_id=(body.get("user") or {}).get("id"),
        thread_ts=message.get("thread_ts") or message_ts,
    )


def _clean_mention(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>\s*", "", text or "").strip()


def _job_id(value: str) -> str | None:
    match = _JOB_ID_RE.search(value or "")
    return match.group(0).upper() if match else None


def _finished_stage_blocks(response: str, *, cancelled: bool = False) -> list[dict]:
    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🚫 Knowledge upload cancelled" if cancelled else "✅ Knowledge published",
                "emoji": True,
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": response[:3000]}},
    ]


async def _handle(event: dict, say, *, strip_mention: bool = False) -> None:
    if event.get("channel") != settings.channel_create_knowledge:
        return
    if event.get("bot_id") or event.get("subtype") in {"bot_message", "message_deleted", "message_changed"}:
        return
    context = _context(event)
    raw = event.get("text") or ""
    text = _clean_mention(raw) if strip_mention else raw.strip()
    try:
        response = await agent.handle(text, context)
    except Exception:
        logger.exception("Create Knowledge request failed request_id=%s", context.request_id)
        response = safe_error_message(context.request_id)

    lower = text.lower()
    response_job_id = _job_id(response)

    # Staged uploads are button-first. Typed confirm/cancel commands remain a fallback.
    if context.files and "*Upload staged — not yet searchable*" in response:
        incident = "ServiceNow Incident export detected" in response
        blocks = staged_incident_blocks(response) if incident else staged_upload_blocks(response)
        await say(text=response, blocks=blocks, thread_ts=context.thread_ts)
        return

    # Typed confirmation fallback: post a persistent build card for the worker to update.
    if lower.startswith("confirm ") and response_job_id:
        job = job_store.get(response_job_id)
        if job:
            posted = await say(
                text=response,
                blocks=queued_blocks(job),
                thread_ts=context.thread_ts,
            )
            message_ts = str(posted.get("ts") or "")
            if message_ts:
                job_store.set_progress_message(response_job_id, message_ts)
            return

    # A manual status request renders the same state model used by the live card.
    if lower.startswith("status "):
        requested_job_id = _job_id(text) or response_job_id
        job = job_store.get(requested_job_id) if requested_job_id else None
        if job:
            await say(
                text=response,
                blocks=build_blocks(job, job.get("progress") or {}),
                thread_ts=context.thread_ts,
            )
            return

    await say(text=response, thread_ts=context.thread_ts)


async def _stage_decision(ack, body, client, *, command: str) -> None:
    """Route a Block Kit decision through the same agent contract as typed commands."""
    await ack()
    action = (body.get("actions") or [{}])[0]
    stage_id = str(action.get("value") or "").strip()
    context = _action_context(body)
    message = body.get("message") or {}
    message_ts = str(message.get("ts") or (body.get("container") or {}).get("message_ts") or "")

    try:
        response = await agent.handle(f"{command} {stage_id}", context)
    except Exception:
        logger.exception("Create Knowledge button action failed request_id=%s", context.request_id)
        response = safe_error_message(context.request_id)

    if not context.channel_id or not message_ts:
        logger.warning("Create Knowledge action missing Slack message context stage_id=%s", stage_id)
        return

    if command == "confirm":
        response_job_id = _job_id(response)
        if response_job_id:
            job = job_store.get(response_job_id)
            if job:
                await client.chat_update(
                    channel=context.channel_id,
                    ts=message_ts,
                    text=response,
                    blocks=queued_blocks(job),
                )
                job_store.set_progress_message(response_job_id, message_ts)
                return
        if response.startswith("Confirmed `"):
            await client.chat_update(
                channel=context.channel_id,
                ts=message_ts,
                text=response,
                blocks=_finished_stage_blocks(response),
            )
            return
    elif command == "cancel" and response.startswith("Cancelled `"):
        await client.chat_update(
            channel=context.channel_id,
            ts=message_ts,
            text=response,
            blocks=_finished_stage_blocks(response, cancelled=True),
        )
        return

    # Denials/errors should not destroy the actionable card for the authorized uploader.
    user_id = (body.get("user") or {}).get("id")
    if user_id:
        await client.chat_postEphemeral(channel=context.channel_id, user=user_id, text=response)


@app.action(ids.CREATE_KNOWLEDGE_CONFIRM_STAGE)
async def confirm_stage_button(ack, body, client):
    await _stage_decision(ack, body, client, command="confirm")


@app.action(ids.CREATE_KNOWLEDGE_CANCEL_STAGE)
async def cancel_stage_button(ack, body, client):
    await _stage_decision(ack, body, client, command="cancel")


@app.event("message")
async def handle_message(event, say):
    # Explicit app mentions arrive through app_mention as well. Avoid duplicate handling.
    if "<@" in (event.get("text") or ""):
        return
    await _handle(event, say)


@app.event("app_mention")
async def handle_mention(event, say):
    await _handle(event, say, strip_mention=True)


async def _run() -> None:
    handler = AsyncSocketModeHandler(app, APP_TOKEN)
    outbox_task = asyncio.create_task(candidate_outbox_loop(app.client, candidate_store))
    try:
        await handler.start_async()
    finally:
        outbox_task.cancel()
        try:
            await outbox_task
        except asyncio.CancelledError:
            pass


def start() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    missing = []
    if not BOT_TOKEN:
        missing.append("CREATE_KNOWLEDGE_SLACK_BOT_TOKEN")
    if not APP_TOKEN:
        missing.append("CREATE_KNOWLEDGE_SLACK_APP_TOKEN")
    if not settings.channel_create_knowledge:
        missing.append("CHANNEL_CREATE_KNOWLEDGE")
    if missing:
        raise RuntimeError("Create Knowledge configuration missing: " + ", ".join(missing))
    logger.info("Starting standalone Create Knowledge agent for %s", settings.channel_create_knowledge)
    asyncio.run(_run())


if __name__ == "__main__":
    start()

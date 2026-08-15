"""Dedicated Socket Mode runtime for the Create Knowledge agent."""

from __future__ import annotations

import asyncio
import logging
import os
import re

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
from src.bot.create_knowledge_progress import build_blocks, queued_blocks  # noqa: E402
from src.bot.create_knowledge_staged import staged_incident_blocks  # noqa: E402
from src.core.config import settings  # noqa: E402
from src.core.context import RequestContext  # noqa: E402
from src.core.errors import safe_error_message  # noqa: E402
from src.knowledge.incident_ingest_jobs import IncidentIngestJobStore  # noqa: E402

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("CREATE_KNOWLEDGE_SLACK_BOT_TOKEN", "").strip()
APP_TOKEN = os.getenv("CREATE_KNOWLEDGE_SLACK_APP_TOKEN", "").strip()
app = AsyncApp(token=BOT_TOKEN or "xoxb-not-configured")
agent = KnowledgeIngestAgent()
job_store = IncidentIngestJobStore()
_JOB_ID_RE = re.compile(r"\bING-[A-Z0-9]+\b", re.I)


def _context(event: dict) -> RequestContext:
    return RequestContext.from_slack(
        channel_id=event.get("channel", ""),
        user_id=event.get("user"),
        thread_ts=event.get("thread_ts") or event.get("ts"),
        files=event.get("files", []),
    )


def _clean_mention(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>\s*", "", text or "").strip()


def _job_id(value: str) -> str | None:
    match = _JOB_ID_RE.search(value or "")
    return match.group(0).upper() if match else None


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

    # Before confirmation, show both the CSV profile and the build pipeline the user is approving.
    if context.files and "ServiceNow Incident export detected" in response:
        await say(
            text=response,
            blocks=staged_incident_blocks(response),
            thread_ts=context.thread_ts,
        )
        return

    # Confirmation becomes the single persistent build card that the worker updates.
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
    await AsyncSocketModeHandler(app, APP_TOKEN).start_async()


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

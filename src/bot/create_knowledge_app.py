"""Dedicated Socket Mode runtime for the Create Knowledge agent."""

from __future__ import annotations

import asyncio
import logging
import os

# The shared Settings object and Slack file downloader use SLACK_* variables.
# Map the dedicated app credentials before importing application modules so this
# process is fully isolated from Frontend Support and the legacy multi-agent app.
_create_bot = os.getenv("CREATE_KNOWLEDGE_SLACK_BOT_TOKEN", "").strip()
_create_app = os.getenv("CREATE_KNOWLEDGE_SLACK_APP_TOKEN", "").strip()
if _create_bot:
    os.environ["SLACK_BOT_TOKEN"] = _create_bot
if _create_app:
    os.environ["SLACK_APP_TOKEN"] = _create_app

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from src.agents.knowledge_ingest import KnowledgeIngestAgent
from src.core.config import settings
from src.core.context import RequestContext
from src.core.errors import safe_error_message

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("CREATE_KNOWLEDGE_SLACK_BOT_TOKEN", "").strip()
APP_TOKEN = os.getenv("CREATE_KNOWLEDGE_SLACK_APP_TOKEN", "").strip()
app = AsyncApp(token=BOT_TOKEN or "xoxb-not-configured")
agent = KnowledgeIngestAgent()


def _context(event: dict) -> RequestContext:
    return RequestContext.from_slack(
        channel_id=event.get("channel", ""),
        user_id=event.get("user"),
        thread_ts=event.get("thread_ts") or event.get("ts"),
        files=event.get("files", []),
    )


async def _handle(event: dict, say) -> None:
    if event.get("channel") != settings.channel_create_knowledge:
        return
    if event.get("bot_id") or event.get("subtype") in {"bot_message", "message_deleted"}:
        return
    context = _context(event)
    text = (event.get("text") or "").strip()
    try:
        response = await agent.handle(text, context)
    except Exception:
        logger.exception("Create Knowledge request failed request_id=%s", context.request_id)
        response = safe_error_message(context.request_id)
    await say(text=response, thread_ts=context.thread_ts)


@app.event("message")
async def handle_message(event, say):
    # Explicit app mentions arrive through app_mention as well. Avoid duplicate handling.
    if "<@" in (event.get("text") or ""):
        return
    await _handle(event, say)


@app.event("app_mention")
async def handle_mention(event, say):
    await _handle(event, say)


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

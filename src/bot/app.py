"""Slack Bolt application entrypoint and event routing."""

from __future__ import annotations

import asyncio
import logging
import re

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from src.bot.blockkit.actions import attach_confirm_cancel
from src.bot.interactivity import register as register_interactivity
from src.bot.readiness import validate_slack_readiness
from src.bot.router import route_message
from src.core.config import settings
from src.core.context import RequestContext
from src.core.errors import safe_error_message

logger = logging.getLogger(__name__)

app = AsyncApp(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)

register_interactivity(app)


def _clean_mention_text(text: str) -> str:
    """Remove the bot mention from the message text."""
    return re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()


def _context_from_event(event: dict) -> RequestContext:
    return RequestContext.from_slack(
        channel_id=event.get("channel", ""),
        user_id=event.get("user"),
        thread_ts=event.get("thread_ts") or event.get("ts"),
        files=event.get("files", []),
    )


@app.event("app_mention")
async def handle_app_mention(event, say):
    """Route @mentions to the appropriate agent based on channel."""
    context = _context_from_event(event)
    text = _clean_mention_text(event.get("text", ""))

    logger.info(
        "Mention request_id=%s channel=%s user=%s text=%s",
        context.request_id,
        context.channel_id,
        context.user_id,
        text[:80],
    )

    try:
        response = await route_message(text, context)
    except Exception:
        logger.exception("Request failed request_id=%s", context.request_id)
        response = safe_error_message(context.request_id)

    await say(text=response, thread_ts=context.thread_ts, blocks=attach_confirm_cancel(response))


@app.event("message")
async def handle_message(event, say):
    """Auto-process file uploads only in the configured knowledge-upload channel."""
    if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return

    if event.get("text") and "<@" in event.get("text", ""):
        return

    context = _context_from_event(event)
    text = event.get("text", "") or ""

    if context.files and context.channel_id == settings.channel_knowledge_uploads:
        logger.info(
            "Knowledge upload request_id=%s files=%s",
            context.request_id,
            [f.get("name") for f in context.files],
        )
        try:
            response = await route_message(
                text or "Please ingest the uploaded file(s).",
                context,
            )
        except Exception:
            logger.exception("Knowledge upload failed request_id=%s", context.request_id)
            response = safe_error_message(context.request_id)
        await say(text=response, thread_ts=context.thread_ts)


async def _start_async() -> None:
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    await handler.start_async()


def start() -> None:
    """Validate configuration and start the Slack bot using asynchronous Socket Mode."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    readiness = validate_slack_readiness(settings)
    for warning in readiness.warnings:
        logger.warning("Slack readiness: %s", warning)
    readiness.require_ready()

    logger.info("Starting Knowledge Management by Slack bot...")
    logger.info(
        "Configured channels → frontend_support=%s, inventory=%s, work_management=%s, knowledge_uploads=%s",
        settings.channel_frontend_support,
        settings.channel_inventory,
        settings.channel_work_management,
        settings.channel_knowledge_uploads,
    )
    asyncio.run(_start_async())


if __name__ == "__main__":
    start()

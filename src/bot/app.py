"""Slack Bolt application entrypoint and event routing."""

from __future__ import annotations

import asyncio
import logging
import re

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.bot.router import route_message, route_reaction
from src.core.config import settings

logger = logging.getLogger(__name__)

app = App(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)


def _clean_mention_text(text: str) -> str:
    """Remove the bot mention from the message text."""
    # Slack mentions look like <@U12345678>
    return re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()


def _run_async(coro):
    """Run an async coroutine from a sync Slack Bolt handler."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Fallback for environments that already have a running loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@app.event("app_mention")
def handle_app_mention(event, say):
    """Route @mentions to the appropriate agent based on channel."""
    channel = event.get("channel")
    raw_text = event.get("text", "")
    text = _clean_mention_text(raw_text)
    thread_ts = event.get("thread_ts") or event.get("ts")
    user = event.get("user")
    files = event.get("files", [])

    logger.info("Mention in channel %s from user %s: %s", channel, user, text[:80])

    try:
        response = _run_async(
            route_message(
                channel_id=channel,
                text=text,
                user=user,
                files=files,
                thread_ts=thread_ts,
            )
        )
    except Exception as e:
        logger.exception("Error while routing message")
        response = f"Sorry, something went wrong while processing your request.\n`{type(e).__name__}: {e}`"

    say(text=response, thread_ts=thread_ts)


@app.event("message")
def handle_message(event, say):
    """
    Handle messages in monitored channels.

    - In #knowledge-uploads: treat file uploads as ingest requests.
    - In other agent channels: only respond to @mentions (handled above)
      or explicit thread follow-ups if desired later.
    """
    # Ignore bot messages and message changes
    if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return

    # Ignore messages that are already handled as app_mentions
    if event.get("text") and "<@" in event.get("text", ""):
        return

    channel = event.get("channel")
    files = event.get("files", [])
    text = event.get("text", "") or ""
    thread_ts = event.get("thread_ts") or event.get("ts")
    user = event.get("user")

    # Only auto-react to file uploads in the knowledge-uploads channel
    if files and channel == settings.channel_knowledge_uploads:
        logger.info(
            "File(s) detected in knowledge-uploads: %s",
            [f.get("name") for f in files],
        )
        try:
            response = _run_async(
                route_message(
                    channel_id=channel,
                    text=text or "Please ingest the uploaded file(s).",
                    user=user,
                    files=files,
                    thread_ts=thread_ts,
                )
            )
            say(text=response, thread_ts=thread_ts)
        except Exception as e:
            logger.exception("Error while handling knowledge upload")
            say(
                text=f"Sorry, I could not process the upload.\n`{type(e).__name__}: {e}`",
                thread_ts=thread_ts,
            )


@app.event("reaction_added")
def handle_reaction_added(event, client):
    """
    Route emoji reactions to the owning agent.

    Currently only #work-management acts on reactions (✅/❌ approve-reject,
    🚧/⛔/✔️ execution status - see src/agents/work_management/reactions.py).
    Other agents' default BaseAgent.handle_reaction() returns None, so this
    is a silent no-op everywhere else.
    """
    item = event.get("item", {})
    channel = item.get("channel")
    if item.get("type") != "message" or not channel:
        return

    logger.info(
        "Reaction '%s' by %s on %s in %s", event.get("reaction"), event.get("user"), item.get("ts"), channel
    )

    try:
        response = _run_async(route_reaction(channel, event))
    except Exception as e:
        logger.exception("Error while routing reaction")
        response = f"Sorry, something went wrong handling that reaction.\n`{type(e).__name__}: {e}`"

    if response:
        client.chat_postMessage(channel=channel, thread_ts=item.get("ts"), text=response)


def start():
    """Start the Slack bot using Socket Mode."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting Knowledge Management by Slack bot...")
    logger.info(
        "Configured channels → frontend_support=%s, inventory=%s, work_management=%s, knowledge_uploads=%s",
        settings.channel_frontend_support,
        settings.channel_inventory,
        settings.channel_work_management,
        settings.channel_knowledge_uploads,
    )
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()


if __name__ == "__main__":
    start()

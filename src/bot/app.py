"""Slack Bolt application entrypoint and event routing."""

import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.core.config import settings

logger = logging.getLogger(__name__)

app = App(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)


@app.event("app_mention")
def handle_app_mention(event, say, client):
    """Route @mentions to the appropriate agent based on channel."""
    channel = event.get("channel")
    text = event.get("text", "")
    thread_ts = event.get("thread_ts") or event.get("ts")
    user = event.get("user")

    logger.info("Received mention in channel %s from user %s", channel, user)

    # TODO: Route to the correct agent based on channel ID
    # For now just acknowledge
    say(
        text=f"Received your message. Agent routing will be implemented next.\n> {text}",
        thread_ts=thread_ts,
    )


@app.event("message")
def handle_message(event, say):
    """Handle messages (including file shares) in monitored channels."""
    # Ignore bot messages and message changes
    if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return

    channel = event.get("channel")
    files = event.get("files", [])

    if files and channel == settings.channel_knowledge_uploads:
        # TODO: Hand off to Knowledge Ingest Agent
        logger.info("File(s) detected in knowledge-uploads channel: %s", [f.get("name") for f in files])


def start():
    """Start the Slack bot using Socket Mode."""
    logging.basicConfig(level=settings.log_level)
    logger.info("Starting Knowledge Management by Slack bot...")
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()


if __name__ == "__main__":
    start()

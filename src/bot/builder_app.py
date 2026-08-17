"""Dedicated Slack Socket Mode runtime for the natural-language Builder channel."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from src.agents.builder import BuilderAgent
from src.core.config import settings
from src.core.context import RequestContext
from src.core.errors import safe_error_message

logger = logging.getLogger(__name__)

app = AsyncApp(
    token=settings.builder_slack_bot_token or settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)
agent = BuilderAgent()

_SEEN_LIMIT = 4096
_seen_order: deque[str] = deque()
_seen: set[str] = set()
_MENTION_RE = re.compile(r"<@[A-Z0-9]+>\s*")


def _csv_ids(value: str) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def trusted_sender_ids() -> set[str]:
    return _csv_ids(settings.builder_trusted_slack_sender_ids)


def event_sender_id(event: dict) -> str | None:
    return event.get("user") or event.get("bot_id") or event.get("app_id")


def is_bot_event(event: dict) -> bool:
    return bool(event.get("bot_id") or event.get("app_id") or event.get("subtype") == "bot_message")


def is_trusted_event(event: dict) -> bool:
    sender = event_sender_id(event)
    if not sender:
        return False
    if is_bot_event(event):
        identities = {event.get("user"), event.get("bot_id"), event.get("app_id")}
        return bool({item for item in identities if item} & trusted_sender_ids())
    return sender in _csv_ids(settings.builder_agent_allowed_user_ids)


def event_key(event: dict) -> str | None:
    if event.get("client_msg_id"):
        return f"client:{event['client_msg_id']}"
    channel = event.get("channel")
    ts = event.get("ts")
    if channel and ts:
        return f"message:{channel}:{ts}"
    return None


def mark_first_delivery(event: dict) -> bool:
    key = event_key(event)
    if not key:
        return True
    if key in _seen:
        return False
    _seen.add(key)
    _seen_order.append(key)
    while len(_seen_order) > _SEEN_LIMIT:
        _seen.discard(_seen_order.popleft())
    return True


def clean_text(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


async def _thread_prompt(event: dict, latest_text: str) -> str:
    root_ts = event.get("thread_ts")
    if not root_ts:
        return latest_text
    try:
        response = await app.client.conversations_replies(
            channel=event["channel"], ts=root_ts, limit=30, inclusive=True
        )
    except Exception:
        logger.exception("Could not load Builder thread context root_ts=%s", root_ts)
        return latest_text

    turns: list[str] = []
    for item in response.get("messages", [])[-24:]:
        body = clean_text(item.get("text", ""))
        if not body or body.startswith("[Builder status]"):
            continue
        role = "Builder" if is_bot_event(item) else "User"
        turns.append(f"{role}: {body}")
    history = "\n".join(turns)[-12000:]
    return f"Slack Builder thread context (oldest to newest):\n{history}\n\nLatest request:\n{latest_text}"


async def handle_builder_event(event: dict, say) -> None:
    if event.get("channel") != settings.channel_builder_agent:
        return
    if event.get("subtype") in {"message_changed", "message_deleted"}:
        return
    if not mark_first_delivery(event):
        logger.info("Builder ingress duplicate ignored key=%s", event_key(event))
        return
    if not is_trusted_event(event):
        logger.warning(
            "Builder ingress rejected sender=%s bot=%s channel=%s",
            event_sender_id(event),
            is_bot_event(event),
            event.get("channel"),
        )
        return

    text = clean_text(event.get("text", ""))
    if not text:
        return

    root_ts = event.get("thread_ts") or event.get("ts")
    sender = event_sender_id(event)
    context = RequestContext.from_slack(
        channel_id=event["channel"], user_id=sender, thread_ts=root_ts
    )
    prompt = await _thread_prompt(event, text)
    try:
        response = await agent.handle(prompt, context)
    except Exception:
        logger.exception("Builder ingress failed request_id=%s", context.request_id)
        response = safe_error_message(context.request_id)
    await say(text=response, thread_ts=root_ts)


@app.event("message")
async def on_message(event, say):
    await handle_builder_event(event, say)


@app.event("app_mention")
async def on_mention(event, say):
    await handle_builder_event(event, say)


async def _start_async() -> None:
    handler = AsyncSocketModeHandler(app, settings.builder_slack_app_token)
    await handler.start_async()


def start() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if not settings.channel_builder_agent:
        raise RuntimeError("CHANNEL_BUILDER_AGENT is required for the dedicated Builder runtime")
    if not settings.builder_agent_allowed_user_ids:
        raise RuntimeError("BUILDER_AGENT_ALLOWED_USER_IDS must contain at least one human user")
    if not settings.builder_slack_bot_token:
        raise RuntimeError("BUILDER_SLACK_BOT_TOKEN is required for the dedicated Builder runtime")
    if not settings.builder_slack_app_token:
        raise RuntimeError("BUILDER_SLACK_APP_TOKEN is required for the dedicated Builder runtime")
    if settings.builder_slack_app_token == settings.slack_app_token:
        raise RuntimeError("BUILDER_SLACK_APP_TOKEN must be different from the generic SLACK_APP_TOKEN")
    logger.info(
        "Starting dedicated Builder Slack runtime channel=%s trusted_external_senders=%s",
        settings.channel_builder_agent,
        len(trusted_sender_ids()),
    )
    asyncio.run(_start_async())


if __name__ == "__main__":
    start()

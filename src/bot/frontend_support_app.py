"""Dedicated Slack Socket Mode runtime for the Frontend Support agent."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from src.agents.frontend_support.agent import INSUFFICIENT_EVIDENCE_RESPONSE
from src.agents.frontend_support.collaboration import MessageKind
from src.agents.frontend_support.conversation import (
    clean_mention_text,
    compose_thread_query,
    looks_like_support,
)
from src.agents.frontend_support.voice import VoiceTranscriptionError, transcribe_first_voice_note
from src.bot.frontend_actions import build_resolution_capture_blocks
from src.bot.frontend_interactivity import get_service, register as register_frontend_interactivity
from src.bot.router import route_frontend_support
from src.core.config import settings
from src.core.context import RequestContext
from src.core.errors import safe_error_message

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("FRONTEND_SUPPORT_SLACK_BOT_TOKEN", "").strip()
APP_TOKEN = os.getenv("FRONTEND_SUPPORT_SLACK_APP_TOKEN", "").strip()

app = AsyncApp(token=BOT_TOKEN or "xoxb-not-configured")
register_frontend_interactivity(app)


def _context(event: dict) -> RequestContext:
    return RequestContext.from_slack(
        channel_id=event.get("channel", ""),
        user_id=event.get("user"),
        thread_ts=event.get("thread_ts") or event.get("ts"),
        files=event.get("files", []),
    )


async def _text(event: dict) -> str:
    typed = (event.get("text") or "").strip()
    transcript = await transcribe_first_voice_note(event.get("files", []))
    if typed and transcript:
        return f"{typed}\n\nVoice note transcript: {transcript}"
    return transcript or typed


async def _route_with_timing(query: str, context: RequestContext, *, lane: str) -> str:
    started = time.perf_counter()
    try:
        return await route_frontend_support(query, context)
    finally:
        logger.info(
            "Frontend Support latency request_id=%s lane=%s total_seconds=%.3f",
            context.request_id,
            lane,
            time.perf_counter() - started,
        )


async def _private(event: dict, say, context: RequestContext, text: str) -> bool:
    if event.get("channel_type") != "im" or not text or not event.get("user"):
        return False
    query = get_service().observe_private(
        channel_id=context.channel_id,
        message_ts=event.get("ts", ""),
        user_id=event["user"],
        text=text,
    )
    try:
        response = await _route_with_timing(query, context, lane="private")
    except Exception:
        logger.exception("Private Frontend Support request failed")
        response = safe_error_message(context.request_id)
    await say(text=response)
    return True


async def _public(event: dict, say, context: RequestContext, text: str) -> bool:
    if context.channel_id != settings.channel_frontend_support or not text or not event.get("user"):
        return False
    message_ts = event.get("ts", "")
    root_ts = event.get("thread_ts") or message_ts
    if not message_ts or not root_ts:
        return True

    service = get_service()
    decision = service.observe(
        channel_id=context.channel_id,
        message_ts=message_ts,
        thread_ts=event.get("thread_ts"),
        user_id=event["user"],
        text=text,
    )

    logger.info(
        "Frontend Support decision request_id=%s kind=%s invoke=%s support_fallback=%s thread=%s",
        context.request_id,
        decision.kind,
        decision.invoke_agent,
        looks_like_support(text),
        root_ts,
    )

    if decision.kind == MessageKind.ASSISTANT_SUPPRESS:
        await say(
            text="Got it — I'll keep listening and remembering the thread, but I'll stay out unless you call me back in.",
            thread_ts=root_ts,
        )
        return True
    if decision.kind == MessageKind.ASSISTANT_RESUME:
        await say(text="I'm back in. Carry on — I'll help where I can add value.", thread_ts=root_ts)
        return True
    if decision.prompt_for_capture:
        state = service.store.get_thread(context.channel_id, root_ts)
        if not state.incident_number:
            await say(
                text="Looks resolved. Add the ServiceNow INC number when convenient so we can capture the fix properly.",
                thread_ts=root_ts,
            )
            return True
        await say(
            text="That looks like a possible resolution. Capture it as reusable operational knowledge?",
            thread_ts=root_ts,
            blocks=build_resolution_capture_blocks(context.channel_id, root_ts),
        )
        return True

    # The cheap collaboration classifier intentionally stays conservative. A
    # broad technical safety net catches natural device/peripheral phrasing
    # such as "Bluetooth headset isn't connecting" without requiring users to
    # write ticket-like prompts.
    should_invoke = decision.invoke_agent or (
        not decision.assistant_suppressed and looks_like_support(text)
    )
    if not should_invoke:
        return True

    agent_context = RequestContext.from_slack(
        channel_id=context.channel_id,
        user_id=context.user_id,
        thread_ts=root_ts,
    )
    query = compose_thread_query(
        service,
        channel_id=context.channel_id,
        thread_ts=root_ts,
        latest_text=decision.agent_query or text,
    )
    try:
        response = await _route_with_timing(query, agent_context, lane="public")
    except Exception:
        logger.exception("Frontend Support proactive response failed")
        return True
    if response == INSUFFICIENT_EVIDENCE_RESPONSE and decision.kind == MessageKind.TROUBLESHOOTING:
        return True
    if decision.prompt_for_incident and response != INSUFFICIENT_EVIDENCE_RESPONSE:
        response += "\n\n_If this is a ServiceNow incident, add the INC number when convenient so the learning stays referenceable._"
    await say(text=response, thread_ts=root_ts)
    return True


@app.event("message")
async def handle_message(event, say):
    if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return
    # app_mention is delivered separately. Skipping mention messages here
    # prevents two independent answers to the same technician turn.
    if "<@" in (event.get("text") or ""):
        return
    context = _context(event)
    try:
        text = await _text(event)
    except VoiceTranscriptionError as exc:
        await say(text=f"I couldn't transcribe that voice note locally: {exc}")
        return
    if await _private(event, say, context, text):
        return
    await _public(event, say, context, text)


@app.event("app_mention")
async def handle_mention(event, say):
    """Resolve an explicit help request against its complete support thread."""
    context = _context(event)
    cleaned = clean_mention_text(event.get("text", ""))
    root_ts = event.get("thread_ts") or event.get("ts", "")
    service = get_service()

    # Record the explicit request as another attributed thread contribution.
    # INSERT OR IGNORE in the store makes this safe if Slack also delivered a
    # corresponding ordinary-message event.
    if context.channel_id == settings.channel_frontend_support and event.get("user") and root_ts:
        service.observe(
            channel_id=context.channel_id,
            message_ts=event.get("ts", ""),
            thread_ts=event.get("thread_ts"),
            user_id=event["user"],
            text=cleaned or "help with this",
        )
        query = compose_thread_query(
            service,
            channel_id=context.channel_id,
            thread_ts=root_ts,
            latest_text=cleaned or "help with this",
        )
    else:
        query = cleaned or event.get("text", "")

    try:
        response = await _route_with_timing(query, context, lane="mention")
    except Exception:
        logger.exception("Frontend Support mention failed")
        response = safe_error_message(context.request_id)
    await say(text=response, thread_ts=root_ts or context.thread_ts)


async def _run_socket_mode() -> None:
    """Construct the Socket Mode client only after an asyncio loop is running."""
    handler = AsyncSocketModeHandler(app, APP_TOKEN)
    await handler.start_async()


def start() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    missing = []
    if not BOT_TOKEN:
        missing.append("FRONTEND_SUPPORT_SLACK_BOT_TOKEN")
    if not APP_TOKEN:
        missing.append("FRONTEND_SUPPORT_SLACK_APP_TOKEN")
    if not settings.channel_frontend_support:
        missing.append("CHANNEL_FRONTEND_SUPPORT")
    if missing:
        raise RuntimeError("Frontend Support configuration missing: " + ", ".join(missing))
    logger.info("Starting standalone Frontend Support agent for %s", settings.channel_frontend_support)
    asyncio.run(_run_socket_mode())


if __name__ == "__main__":
    start()

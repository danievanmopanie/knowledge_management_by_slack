"""Dedicated Slack Socket Mode runtime for the Frontend Support agent."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from src.agents.frontend_support.agent import INSUFFICIENT_EVIDENCE_RESPONSE
from src.agents.frontend_support.clarification import ClarificationEngine
from src.agents.frontend_support.collaboration import MessageKind
from src.agents.frontend_support.conversation import (
    clean_mention_text,
    compose_thread_query,
    looks_like_support,
)
from src.agents.frontend_support.voice import VoiceTranscriptionError, transcribe_first_voice_note
from src.bot.frontend_actions import build_resolution_capture_blocks
from src.bot.frontend_interactivity import get_service, register as register_frontend_interactivity
from src.bot.knowledge_governance_interactivity import register as register_knowledge_governance_interactivity
from src.bot.router import route_frontend_support
from src.core.config import settings
from src.core.context import RequestContext
from src.core.errors import safe_error_message

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("FRONTEND_SUPPORT_SLACK_BOT_TOKEN", "").strip()
APP_TOKEN = os.getenv("FRONTEND_SUPPORT_SLACK_APP_TOKEN", "").strip()

app = AsyncApp(token=BOT_TOKEN or "xoxb-not-configured")
register_frontend_interactivity(app)
register_knowledge_governance_interactivity(app)
clarifications = ClarificationEngine()

BUSY_TEXT = "⏳ Working on this — checking the thread and relevant support history…"
WAITING_TEXT = "⏸️ *Waiting for your reply.*"
DONE_TEXT = "✅ *Done — you can continue from here.*"
STREAM_UPDATE_INTERVAL_SECONDS = 0.9
STREAM_MIN_NEW_CHARS = 70


def _with_status(text: str, status: str) -> str:
    """Append a clear terminal/waiting state without duplicating an existing marker."""
    body = text.rstrip()
    if body.endswith(status):
        return body
    return f"{body}\n\n{status}" if body else status


def _normalise_message_event(event: dict) -> dict | None:
    """Return the effective human message for normal and edited Slack events."""
    subtype = event.get("subtype")
    if subtype in {"message_deleted", "bot_message"}:
        return None
    if subtype == "message_changed":
        message = dict(event.get("message") or {})
        if not message or message.get("subtype") == "bot_message" or not message.get("user"):
            return None
        message.setdefault("channel", event.get("channel", ""))
        message.setdefault("channel_type", event.get("channel_type"))
        return message
    return event


def _context(event: dict) -> RequestContext:
    return RequestContext.from_slack(
        channel_id=event.get("channel", ""),
        user_id=event.get("user"),
        thread_ts=event.get("thread_ts") or event.get("ts"),
        files=event.get("files", []),
        roles=("general_support_fallback",),
    )


async def _text(event: dict) -> str:
    """Combine typed text with a locally transcribed first voice note when present."""
    typed = (event.get("text") or "").strip()
    transcript = await transcribe_first_voice_note(event.get("files", []))
    if typed and transcript:
        return f"{typed}\n\nVoice note transcript: {transcript}"
    return transcript or typed


async def _route_with_timing(
    query: str,
    context: RequestContext,
    *,
    lane: str,
    on_chunk=None,
) -> str:
    started = time.perf_counter()
    try:
        return await route_frontend_support(query, context, on_chunk=on_chunk)
    finally:
        logger.info(
            "Frontend Support latency request_id=%s lane=%s total_seconds=%.3f",
            context.request_id,
            lane,
            time.perf_counter() - started,
        )


async def _start_progress(
    client,
    *,
    channel_id: str,
    thread_ts: str | None = None,
) -> str | None:
    """Post an immediate acknowledgement so technicians know work has started."""
    try:
        payload = {"channel": channel_id, "text": BUSY_TEXT}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        result = await client.chat_postMessage(**payload)
        return result.get("ts")
    except Exception:
        logger.exception("Could not post Frontend Support progress indicator")
        return None


async def _finish_progress(
    client,
    *,
    channel_id: str,
    message_ts: str | None,
    text: str,
    blocks: list[dict] | None = None,
) -> None:
    """Replace the acknowledgement with clarification UI or the final answer."""
    if not message_ts:
        return
    try:
        await client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=text,
            blocks=blocks or [],
        )
    except Exception:
        logger.exception("Could not update Frontend Support progress message")


def _stream_callback(client, *, channel_id: str, message_ts: str | None):
    """Return a throttled callback that streams accumulated LLM text into one Slack message."""
    if not message_ts:
        return None
    last_update = 0.0
    last_length = 0
    lock = asyncio.Lock()

    async def update(partial: str) -> None:
        nonlocal last_update, last_length
        if not partial:
            return
        now = time.monotonic()
        enough_text = len(partial) - last_length >= STREAM_MIN_NEW_CHARS
        enough_time = now - last_update >= STREAM_UPDATE_INTERVAL_SECONDS
        first_meaningful = last_length == 0 and len(partial) >= 30
        if not first_meaningful and not (enough_text and enough_time):
            return
        async with lock:
            now = time.monotonic()
            if last_length and (
                len(partial) - last_length < STREAM_MIN_NEW_CHARS
                or now - last_update < STREAM_UPDATE_INTERVAL_SECONDS
            ):
                return
            try:
                await client.chat_update(
                    channel=channel_id,
                    ts=message_ts,
                    text=partial.rstrip() + "\n\n_Still working…_",
                    blocks=[],
                )
                last_update = now
                last_length = len(partial)
            except Exception:
                logger.exception("Could not stream Frontend Support partial response")

    return update


async def _private(event: dict, say, client, context: RequestContext, text: str) -> bool:
    """Handle one-to-one technician coaching without exposing the conversation publicly."""
    if event.get("channel_type") != "im" or not text or not event.get("user"):
        return False
    query = get_service().observe_private(
        channel_id=context.channel_id,
        message_ts=event.get("ts", ""),
        user_id=event["user"],
        text=text,
    )
    private_context = RequestContext.from_slack(
        channel_id=context.channel_id,
        user_id=context.user_id,
        thread_ts=context.thread_ts,
        roles=("private_coach", "general_support_fallback"),
    )
    progress_ts = await _start_progress(client, channel_id=context.channel_id)
    stream = _stream_callback(
        client,
        channel_id=context.channel_id,
        message_ts=progress_ts,
    )
    try:
        response = await _route_with_timing(
            query,
            private_context,
            lane="private",
            on_chunk=stream,
        )
    except Exception:
        logger.exception("Private Frontend Support request failed")
        response = safe_error_message(context.request_id)
    final_text = _with_status(response, DONE_TEXT)
    if progress_ts:
        await _finish_progress(
            client,
            channel_id=context.channel_id,
            message_ts=progress_ts,
            text=final_text,
        )
    else:
        await say(text=final_text)
    return True


async def _public(event: dict, say, client, context: RequestContext, text: str) -> bool:
    """Observe the channel continuously, but invoke only when the thread needs support."""
    if context.channel_id != settings.channel_frontend_support or not text or not event.get("user"):
        return False

    message_ts = event.get("ts", "")
    root_ts = event.get("thread_ts") or message_ts
    if not message_ts or not root_ts:
        return True

    service = get_service()
    clarification_answer = None
    if event.get("thread_ts"):
        clarification_answer = clarifications.store.consume_free_text(
            context.channel_id, root_ts, text
        )

    decision = service.observe(
        channel_id=context.channel_id,
        message_ts=message_ts,
        thread_ts=event.get("thread_ts"),
        user_id=event["user"],
        text=text,
    )

    logger.info(
        "Frontend Support decision request_id=%s kind=%s invoke=%s support_fallback=%s clarification=%s thread=%s",
        context.request_id,
        decision.kind,
        decision.invoke_agent,
        looks_like_support(text),
        bool(clarification_answer),
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

    should_invoke = decision.invoke_agent or bool(clarification_answer) or (
        not decision.assistant_suppressed and looks_like_support(text)
    )
    if not should_invoke:
        return True

    progress_ts = await _start_progress(
        client,
        channel_id=context.channel_id,
        thread_ts=root_ts,
    )
    query = compose_thread_query(
        service,
        channel_id=context.channel_id,
        thread_ts=root_ts,
        latest_text=decision.agent_query or text,
    )

    question = clarifications.next_question(context.channel_id, root_ts, query)
    if question is not None:
        round_number = clarifications.store.ask(context.channel_id, root_ts, question.key)
        clarification_text = _with_status(
            "I need one detail before I search the incident history "
            f"(clarification {round_number}/3): {question.question}",
            WAITING_TEXT,
        )
        if progress_ts:
            await _finish_progress(
                client,
                channel_id=context.channel_id,
                message_ts=progress_ts,
                text=clarification_text,
                blocks=clarifications.blocks(question, context.channel_id, root_ts),
            )
        else:
            await say(
                text=clarification_text,
                blocks=clarifications.blocks(question, context.channel_id, root_ts),
                thread_ts=root_ts,
            )
        return True

    agent_context = RequestContext.from_slack(
        channel_id=context.channel_id,
        user_id=context.user_id,
        thread_ts=root_ts,
        roles=("general_support_fallback",),
    )
    stream = _stream_callback(
        client,
        channel_id=context.channel_id,
        message_ts=progress_ts,
    )
    try:
        response = await _route_with_timing(
            query,
            agent_context,
            lane="public",
            on_chunk=stream,
        )
    except Exception:
        logger.exception("Frontend Support proactive response failed")
        response = safe_error_message(context.request_id)

    if response == INSUFFICIENT_EVIDENCE_RESPONSE and decision.kind == MessageKind.TROUBLESHOOTING:
        response = (
            "I checked the available support history but don't have a reliable next step yet. "
            "Add any new symptom or result and I'll keep working with the thread."
        )
    if decision.prompt_for_incident and response != INSUFFICIENT_EVIDENCE_RESPONSE:
        response += (
            "\n\n_If this is a ServiceNow incident, add the INC number when convenient "
            "so the learning stays referenceable._"
        )

    final_text = _with_status(response, DONE_TEXT)
    if progress_ts:
        await _finish_progress(
            client,
            channel_id=context.channel_id,
            message_ts=progress_ts,
            text=final_text,
        )
    else:
        await say(text=final_text, thread_ts=root_ts)
    return True


@app.event("message")
async def handle_message(event, say, client):
    effective_event = _normalise_message_event(event)
    if effective_event is None:
        return
    # Slack also emits app_mention for mention-bearing messages; let that handler own them.
    if "<@" in (effective_event.get("text") or ""):
        return
    context = _context(effective_event)
    try:
        text = await _text(effective_event)
    except VoiceTranscriptionError as exc:
        await say(text=f"I couldn't transcribe that voice note locally: {exc}")
        return
    if await _private(effective_event, say, client, context, text):
        return
    await _public(effective_event, say, client, context, text)


@app.event("app_mention")
async def handle_mention(event, say, client):
    """Resolve an explicit help request against the complete stored support thread."""
    context = _context(event)
    cleaned = clean_mention_text(event.get("text", ""))
    root_ts = event.get("thread_ts") or event.get("ts", "")
    service = get_service()

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

    progress_ts = None
    if root_ts and context.channel_id:
        progress_ts = await _start_progress(
            client,
            channel_id=context.channel_id,
            thread_ts=root_ts,
        )

    question = clarifications.next_question(context.channel_id, root_ts, query) if root_ts else None
    if question is not None:
        round_number = clarifications.store.ask(context.channel_id, root_ts, question.key)
        clarification_text = _with_status(
            "I need one detail before I search the incident history "
            f"(clarification {round_number}/3): {question.question}",
            WAITING_TEXT,
        )
        if progress_ts:
            await _finish_progress(
                client,
                channel_id=context.channel_id,
                message_ts=progress_ts,
                text=clarification_text,
                blocks=clarifications.blocks(question, context.channel_id, root_ts),
            )
        else:
            await say(
                text=clarification_text,
                blocks=clarifications.blocks(question, context.channel_id, root_ts),
                thread_ts=root_ts,
            )
        return

    mention_context = RequestContext.from_slack(
        channel_id=context.channel_id,
        user_id=context.user_id,
        thread_ts=root_ts or context.thread_ts,
        roles=("general_support_fallback",),
    )
    stream = _stream_callback(
        client,
        channel_id=context.channel_id,
        message_ts=progress_ts,
    )
    try:
        response = await _route_with_timing(
            query,
            mention_context,
            lane="mention",
            on_chunk=stream,
        )
    except Exception:
        logger.exception("Frontend Support mention failed")
        response = safe_error_message(context.request_id)

    final_text = _with_status(response, DONE_TEXT)
    if progress_ts:
        await _finish_progress(
            client,
            channel_id=context.channel_id,
            message_ts=progress_ts,
            text=final_text,
        )
    else:
        await say(text=final_text, thread_ts=root_ts or context.thread_ts)


async def _run_socket_mode() -> None:
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

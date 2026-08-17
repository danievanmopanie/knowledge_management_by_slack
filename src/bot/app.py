"""Slack Bolt application entrypoint and event routing."""

from __future__ import annotations

import asyncio
import logging
import re

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from src.agents.frontend_support.agent import (
    INSUFFICIENT_EVIDENCE_RESPONSE,
    is_clarifying_question,
    strip_clarification_marker,
)
from src.agents.frontend_support.collaboration import MessageKind
from src.agents.frontend_support.voice import VoiceTranscriptionError, transcribe_first_voice_note
from src.bot.blockkit.actions import attach_confirm_cancel
from src.bot.frontend_actions import build_resolution_capture_blocks
from src.bot.frontend_interactivity import get_service as get_frontend_service
from src.bot.frontend_interactivity import register as register_frontend_interactivity
from src.bot.interactivity import register as register_interactivity
from src.bot.readiness import validate_slack_readiness
from src.bot.router import route_frontend_support, route_message
from src.core.config import settings
from src.core.context import RequestContext
from src.core.errors import safe_error_message

logger = logging.getLogger(__name__)

app = AsyncApp(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)

register_interactivity(app)
register_frontend_interactivity(app)

BUILDER_STATUS_PREFIX = "[Builder status]"
_BUILDER_CONTROL_RE = re.compile(r"^(status|cancel)\s+\S+\s*$", re.I)


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


async def _resolve_message_text(event: dict) -> str:
    """Return typed text plus a local transcript when a Slack voice note is attached."""
    text = (event.get("text") or "").strip()
    transcript = await transcribe_first_voice_note(event.get("files", []))
    if not transcript:
        return text
    if text:
        return f"{text}\n\nVoice note transcript: {transcript}"
    return transcript


async def _builder_thread_prompt(event: dict, latest_text: str) -> str:
    """Build compact conversational context for a Builder thread.

    Slack remains the source of conversational memory. We include recent human
    and assistant turns, but deliberately exclude Builder progress-card fallback
    text so operational status noise does not become coding context.
    """
    root_ts = event.get("thread_ts")
    if not root_ts:
        return latest_text

    try:
        response = await app.client.conversations_replies(
            channel=event.get("channel", ""),
            ts=root_ts,
            limit=30,
            inclusive=True,
        )
    except Exception:
        logger.exception("Could not load Builder thread context for %s", root_ts)
        return latest_text

    turns: list[str] = []
    for message in response.get("messages", [])[-24:]:
        body = _clean_mention_text((message.get("text") or "").strip())
        if not body or body.startswith(BUILDER_STATUS_PREFIX):
            continue
        is_bot = bool(message.get("bot_id")) or message.get("subtype") == "bot_message"
        role = "Builder" if is_bot else "User"
        turns.append(f"{role}: {body}")

    history = "\n".join(turns)
    if len(history) > 12000:
        history = history[-12000:]
    return (
        "Slack Builder thread context (oldest to newest):\n"
        f"{history}\n\n"
        "Latest request:\n"
        f"{latest_text}"
    )


async def _handle_builder_message(event: dict, say, context: RequestContext, text: str) -> bool:
    """Treat ordinary #builder messages as natural coding-harness turns."""
    if context.channel_id != settings.channel_builder_agent or not text or not event.get("user"):
        return False

    root_ts = event.get("thread_ts") or event.get("ts") or context.thread_ts
    # Keep deterministic controls exact; all other turns receive recent thread
    # context and are interpreted naturally by the coding harness.
    prompt = text if _BUILDER_CONTROL_RE.match(text.strip()) else await _builder_thread_prompt(event, text)
    try:
        response = await route_message(prompt, context)
    except Exception:
        logger.exception("Builder conversation failed request_id=%s", context.request_id)
        response = safe_error_message(context.request_id)
    await say(text=response, thread_ts=root_ts)
    return True


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

    # Builder does not require mentions, but a mention inside #builder should
    # behave exactly like an ordinary conversational turn.
    if await _handle_builder_message(event, say, context, text):
        return

    try:
        response = await route_message(text, context)
    except Exception:
        logger.exception("Request failed request_id=%s", context.request_id)
        response = safe_error_message(context.request_id)

    await say(text=response, thread_ts=context.thread_ts, blocks=attach_confirm_cancel(response))


async def _handle_private_frontend_message(
    event: dict, say, context: RequestContext, text: str
) -> bool:
    """Provide a psychologically safe one-on-one Frontend Support coaching lane in Slack DMs."""
    if event.get("channel_type") != "im" or not text or not event.get("user"):
        return False

    service = get_frontend_service()
    query = service.observe_private(
        channel_id=context.channel_id,
        message_ts=event.get("ts", ""),
        user_id=event["user"],
        text=text,
    )
    try:
        response = await route_frontend_support(query, context)
    except Exception:
        logger.exception("Private frontend coaching failed request_id=%s", context.request_id)
        response = safe_error_message(context.request_id)
    await say(text=response)
    return True


async def _handle_frontend_message(event: dict, say, context: RequestContext, text: str) -> bool:
    """Observe ordinary #frontend-support chat and intervene only when useful."""
    if context.channel_id != settings.channel_frontend_support or not text or not event.get("user"):
        return False

    message_ts = event.get("ts", "")
    root_ts = event.get("thread_ts") or message_ts
    if not message_ts or not root_ts:
        return True

    service = get_frontend_service()
    decision = service.observe(
        channel_id=context.channel_id,
        message_ts=message_ts,
        thread_ts=event.get("thread_ts"),
        user_id=event["user"],
        text=text,
    )

    logger.info(
        "Frontend collaboration request_id=%s kind=%s invoke=%s incident=%s suppressed=%s",
        context.request_id,
        decision.kind,
        decision.invoke_agent,
        decision.incident_number,
        decision.assistant_suppressed,
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
                text=(
                    "Looks like you may have this resolved. Add the ServiceNow incident number when you can "
                    "so we can keep the fix referenceable and capture it properly."
                ),
                thread_ts=root_ts,
            )
            return True
        prompt = "That looks like a possible resolution. Capture it as reusable operational knowledge?"
        await say(
            text=prompt,
            thread_ts=root_ts,
            blocks=build_resolution_capture_blocks(context.channel_id, root_ts),
        )
        return True

    if not decision.invoke_agent:
        return True

    agent_context = RequestContext.from_slack(
        channel_id=context.channel_id,
        user_id=context.user_id,
        thread_ts=root_ts,
    )
    try:
        response = await route_frontend_support(decision.agent_query or text, agent_context)
    except Exception:
        logger.exception("Frontend proactive response failed request_id=%s", context.request_id)
        return True

    if response == INSUFFICIENT_EVIDENCE_RESPONSE and decision.kind == MessageKind.TROUBLESHOOTING:
        return True

    # Remember whether this reply is a clarifying question so a follow-up in the
    # thread routes back to the assistant even if it doesn't read as "technical".
    service.store.set_awaiting_clarification(
        context.channel_id, root_ts, is_clarifying_question(response)
    )
    response = strip_clarification_marker(response)

    if decision.prompt_for_incident and response != INSUFFICIENT_EVIDENCE_RESPONSE:
        response = (
            response.rstrip()
            + "\n\n_If this is a ServiceNow incident, drop the INC number into the thread when convenient so the learning stays referenceable._"
        )

    await say(text=response, thread_ts=root_ts)
    return True


@app.event("message")
async def handle_message(event, say):
    """Observe support collaboration, Builder conversation, voice notes and uploads."""
    if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return

    if event.get("text") and "<@" in event.get("text", ""):
        return

    context = _context_from_event(event)
    try:
        text = await _resolve_message_text(event)
    except VoiceTranscriptionError as exc:
        if (
            context.channel_id in {settings.channel_frontend_support, settings.channel_builder_agent}
            or event.get("channel_type") == "im"
        ):
            await say(text=f"I couldn't transcribe that voice note locally: {exc}")
            return
        text = event.get("text", "") or ""

    if await _handle_private_frontend_message(event, say, context, text):
        return

    if await _handle_frontend_message(event, say, context, text):
        return

    if await _handle_builder_message(event, say, context, text):
        return

    knowledge_channels = {
        channel
        for channel in (settings.channel_knowledge_uploads, settings.channel_create_knowledge)
        if channel
    }
    if context.files and context.channel_id in knowledge_channels:
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
        "Configured channels → frontend_support=%s, inventory=%s, work_management=%s, knowledge_uploads=%s, create_knowledge=%s, builder=%s",
        settings.channel_frontend_support,
        settings.channel_inventory,
        settings.channel_work_management,
        settings.channel_knowledge_uploads,
        settings.channel_create_knowledge,
        settings.channel_builder_agent,
    )
    asyncio.run(_start_async())


if __name__ == "__main__":
    start()

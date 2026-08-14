"""Slack interactivity for frontend-support knowledge capture and knowledge-gap creation."""

from __future__ import annotations

import json
import logging

from slack_bolt.async_app import AsyncApp

from src.agents.frontend_support.collaboration import FrontendCollaborationService
from src.bot.frontend_actions import CAPTURE_RESOLUTION, DISMISS_RESOLUTION
from src.core.config import settings
from src.core.context import RequestContext
from src.knowledge.retrieval_models import RetrievalQuery
from src.knowledge.retriever import HybridRetriever

logger = logging.getLogger(__name__)

_service: FrontendCollaborationService | None = None
_retriever: HybridRetriever | None = None


def get_service() -> FrontendCollaborationService:
    global _service
    if _service is None:
        _service = FrontendCollaborationService()
    return _service


def get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever


def _remove_capture_controls(blocks: list[dict]) -> list[dict]:
    return [
        block
        for block in blocks
        if block.get("block_id") != "frontend_resolution_capture"
    ]


def _knowledge_task_message(task: dict) -> str:
    contributors = ", ".join(task.get("contributors") or []) or "not yet attributed"
    return (
        f"*Knowledge task {task['task_id']} — formal knowledge needed*\n\n"
        f"*Incident:* `{task['incident_number']}`\n"
        f"*Problem / symptom:* {task['symptom'] or '(not captured)'}\n"
        f"*Confirmed field resolution:* {task['resolution']}\n"
        f"*Contributors:* {contributors}\n"
        f"*Why this task was created:* {task['reason']}\n\n"
        "Use the incident history and source support thread as evidence. Validate the fix, exceptions, "
        "preconditions and root cause before publishing formal knowledge.\n\n"
        f"_Source support thread: channel `{task['source_channel_id']}`, thread `{task['source_thread_ts']}`_"
    )


async def _maybe_create_knowledge_gap(
    *,
    client,
    channel_id: str,
    thread_ts: str,
    actor_id: str,
) -> str | None:
    """Create a formal knowledge task when a confirmed field fix has weak governed coverage."""
    service = get_service()
    state = service.store.get_thread(channel_id, thread_ts)
    events = service.store.recent_events(channel_id, thread_ts, limit=50)
    resolution = service.store._resolution_summary(events)
    query = "\n".join(part for part in (state.root_message, resolution) if part).strip()
    if not query:
        return None

    context = RequestContext.from_slack(
        channel_id=channel_id,
        user_id=actor_id,
        thread_ts=thread_ts,
    )
    try:
        result = get_retriever().search(
            RetrievalQuery(text=query, context=context, limit=5, graph_depth=1)
        )
    except Exception:
        logger.exception("Governed knowledge-gap check failed for %s/%s", channel_id, thread_ts)
        return None

    has_strong_formal_coverage = bool(
        result.should_answer and result.confidence_score >= settings.retrieval_strong_confidence
    )
    if has_strong_formal_coverage:
        return None

    reason = (
        "No strong governed knowledge matched this human-confirmed resolution. "
        f"Governed retrieval confidence was {result.confidence_score:.2f}."
    )
    try:
        task = service.create_knowledge_gap_task(
            channel_id,
            thread_ts,
            actor_id,
            reason,
        )
    except Exception:
        logger.exception("Could not create knowledge-gap task for %s/%s", channel_id, thread_ts)
        return None

    target_channel = settings.channel_create_knowledge or settings.channel_knowledge_uploads
    if not target_channel:
        logger.warning(
            "Knowledge task %s created but no create-knowledge channel is configured",
            task["task_id"],
        )
        return task["task_id"]

    await client.chat_postMessage(
        channel=target_channel,
        text=_knowledge_task_message(task),
        mrkdwn=True,
    )
    return task["task_id"]


def register(app: AsyncApp) -> None:
    @app.action(CAPTURE_RESOLUTION)
    async def capture_resolution(ack, body, client):
        await ack()
        action = body["actions"][0]
        payload = json.loads(action["value"])
        channel_id = payload["channel_id"]
        thread_ts = payload["thread_ts"]
        actor_id = body["user"]["id"]
        result = get_service().confirm_knowledge(channel_id, thread_ts, actor_id)

        message = body.get("message") or {}
        message_ts = message.get("ts")
        captured = result.startswith("Captured this resolution")
        if message_ts and captured:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=message.get("text", "Knowledge capture confirmed."),
                blocks=_remove_capture_controls(message.get("blocks", [])),
            )
        await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=result)

        if captured:
            task_id = await _maybe_create_knowledge_gap(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts,
                actor_id=actor_id,
            )
            if task_id:
                await client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=(
                        f"I also opened *{task_id}* for formal curation because this fix is not yet "
                        "strongly covered by governed knowledge. The field resolution remains usable "
                        "as confirmed operational evidence in the meantime."
                    ),
                )

    @app.action(DISMISS_RESOLUTION)
    async def dismiss_resolution(ack, body, client):
        await ack()
        message = body.get("message") or {}
        channel_id = body.get("channel", {}).get("id")
        message_ts = message.get("ts")
        if channel_id and message_ts:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=message.get("text", "Knowledge capture dismissed."),
                blocks=_remove_capture_controls(message.get("blocks", [])),
            )

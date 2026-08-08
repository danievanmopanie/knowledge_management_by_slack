"""Route Slack events to the correct specialised agent based on channel."""

from __future__ import annotations

import logging
from typing import Any

from src.agents.frontend_support import FrontendSupportAgent
from src.agents.inventory import InventoryAgent
from src.agents.knowledge_ingest import KnowledgeIngestAgent
from src.agents.work_management import WorkManagementAgent
from src.core.config import settings

logger = logging.getLogger(__name__)

# Instantiate agents once (stateless for now)
_frontend_support = FrontendSupportAgent()
_inventory = InventoryAgent()
_work_management = WorkManagementAgent()
_knowledge_ingest = KnowledgeIngestAgent()


def _channel_to_agent(channel_id: str | None):
    """Return the agent instance for a given Slack channel ID."""
    if not channel_id:
        return None

    mapping = {
        settings.channel_frontend_support: _frontend_support,
        settings.channel_inventory: _inventory,
        settings.channel_work_management: _work_management,
        settings.channel_knowledge_uploads: _knowledge_ingest,
    }
    return mapping.get(channel_id)


async def route_message(
    channel_id: str,
    text: str,
    user: str | None = None,
    files: list[dict] | None = None,
    thread_ts: str | None = None,
) -> str:
    """
    Route a message to the appropriate agent and return the response text.

    Falls back to a helpful message if the channel is not configured.
    """
    agent = _channel_to_agent(channel_id)

    if agent is None:
        logger.warning("No agent mapped for channel %s", channel_id)
        return (
            "This channel is not yet configured for an agent.\n"
            "Please set the channel IDs in your `.env` file:\n"
            "`CHANNEL_FRONTEND_SUPPORT`, `CHANNEL_INVENTORY`, "
            "`CHANNEL_WORK_MANAGEMENT`, `CHANNEL_KNOWLEDGE_UPLOADS`."
        )

    context: dict[str, Any] = {
        "channel_id": channel_id,
        "user": user,
        "thread_ts": thread_ts,
        "files": files or [],
    }

    logger.info("Routing to agent '%s' for channel %s", agent.name, channel_id)
    return await agent.handle(text, context)

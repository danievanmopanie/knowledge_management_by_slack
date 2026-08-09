"""Route Slack events to the correct specialised agent based on channel."""

from __future__ import annotations

import logging

from src.agents.frontend_support import FrontendSupportAgent
from src.agents.inventory import InventoryAgent
from src.agents.knowledge_ingest import KnowledgeIngestAgent
from src.agents.work_management import WorkManagementAgent
from src.core.config import settings
from src.core.context import RequestContext

logger = logging.getLogger(__name__)

# Instantiate agents once (stateless for now)
_frontend_support = FrontendSupportAgent()
_inventory = InventoryAgent()
_work_management = WorkManagementAgent()
_knowledge_ingest = KnowledgeIngestAgent()


def get_inventory_agent() -> InventoryAgent:
    """Return the shared InventoryAgent singleton (used by Block Kit interactivity handlers)."""
    return _inventory


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


async def route_message(message: str, context: RequestContext) -> str:
    """Route a request to the agent configured for its Slack channel."""
    agent = _channel_to_agent(context.channel_id)

    if agent is None:
        logger.warning(
            "No agent mapped for channel %s request_id=%s",
            context.channel_id,
            context.request_id,
        )
        return (
            "This channel is not yet configured for an agent.\n"
            "Please set the channel IDs in your `.env` file:\n"
            "`CHANNEL_FRONTEND_SUPPORT`, `CHANNEL_INVENTORY`, "
            "`CHANNEL_WORK_MANAGEMENT`, `CHANNEL_KNOWLEDGE_UPLOADS`."
        )

    logger.info(
        "Routing request_id=%s to agent '%s' for channel %s",
        context.request_id,
        agent.name,
        context.channel_id,
    )
    return await agent.handle(message, context)

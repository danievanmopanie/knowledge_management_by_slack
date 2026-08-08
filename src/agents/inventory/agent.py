"""Inventory Management Agent – Slack coordinator for deterministic inventory operations."""

from __future__ import annotations

import logging

from src.agents.base import BaseAgent
from src.core.context import RequestContext
from src.core.errors import safe_error_message
from src.inventory.commands import InventoryCommandService
from src.inventory.domain import InventoryDomainError

logger = logging.getLogger(__name__)

HELP = """*Inventory Agent*

The inventory agent now executes deterministic inventory operations against the local SQLite inventory store.

*Serialized assets*
• `status asset A-1042`
• `store asset A-1042 at SHELF-B3`
• `issue asset A-1042 to U123 customer EMP-42 dedicated`
• `issue asset A-1042 to U123 customer EMP-42 loan until 2026-08-20`
• `return asset A-1042`
• `repair asset A-1042 at REPAIR-CAGE`
• `quarantine asset A-1042 at QUARANTINE-CAGE`
• `retire asset A-1042`
• `dispose asset A-1042`

*Quantity stock*
• `stock MOUSE-01 at STORE-A`
• `low stock`
• `reserve stock MOUSE-01 5 at STORE-A for EMP-42`
• `issue stock MOUSE-01 5 from STORE-A to EMP-42`
• `issue stock MOUSE-01 5 from STORE-A to EMP-42 reservation <id>`
• `return stock MOUSE-01 2 to STORE-A from EMP-42`
• `transfer stock MOUSE-01 10 from STORE-A to STORE-B`
• `count stock MOUSE-01 at STORE-A = 97`

Receiving against purchase orders is implemented in the domain layer; document/photo-assisted Slack receiving is the next workflow slice.
"""


class InventoryAgent(BaseAgent):
    """Slack-facing inventory coordinator that only invokes deterministic domain services."""

    name = "inventory"

    def __init__(self):
        self.commands = InventoryCommandService()

    async def handle(self, message: str, context: RequestContext) -> str:
        text = (message or "").strip()
        if not text and not context.files:
            return HELP
        if text.lower() in {"help", "?", "hi", "hello"}:
            return HELP

        if context.files:
            return (
                "I received the attachment, but file-assisted inventory receiving is not enabled in the "
                "Slack command path yet. Use a deterministic inventory command or type `help`."
            )

        try:
            return self.commands.execute(text, actor=context.user_id or "slack-user")
        except InventoryDomainError as exc:
            return f"Inventory rule prevented that operation: {exc}"
        except Exception:
            logger.exception("Inventory command failed request_id=%s", context.request_id)
            return safe_error_message(context.request_id)

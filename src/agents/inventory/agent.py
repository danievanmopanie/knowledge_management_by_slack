"""Inventory Management Agent – Slack coordinator for deterministic inventory operations."""

from __future__ import annotations

import logging
import re

from src.agents.base import BaseAgent
from src.core.context import RequestContext
from src.core.errors import safe_error_message
from src.inventory.assisted_receiving import AssistedReceivingWorkflow
from src.inventory.commands import InventoryCommandService
from src.inventory.domain import InventoryDomainError
from src.knowledge.file_loader import UploadValidationError, download_slack_file

logger = logging.getLogger(__name__)

HELP = """*Inventory Agent*

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
• `return stock MOUSE-01 2 to STORE-A from EMP-42`
• `transfer stock MOUSE-01 10 from STORE-A to STORE-B`
• `count stock MOUSE-01 at STORE-A = 97`

*Delivery receiving*
Attach a CSV/PDF/DOCX/text delivery note and type:
• `receive PO-2026-0001 at STORE-A`
The agent extracts a proposed receipt and shows discrepancies without changing stock.
Then use:
• `confirm receipt RCV-...`
• `cancel receipt RCV-...`

For CSV delivery notes, use columns such as `po_line_id, sku, quantity, damaged_quantity, serial_numbers`.
"""

RECEIVE_RE = re.compile(r"^receive\s+(?P<po>\S+)\s+at\s+(?P<location>\S+)$", re.I)
CONFIRM_RE = re.compile(r"^confirm\s+receipt\s+(?P<receipt>\S+)$", re.I)
CANCEL_RE = re.compile(r"^cancel\s+receipt\s+(?P<receipt>\S+)$", re.I)


class InventoryAgent(BaseAgent):
    """Slack-facing inventory coordinator invoking deterministic domain services only."""

    name = "inventory"

    def __init__(self):
        self.commands = InventoryCommandService()
        self.receiving = AssistedReceivingWorkflow()

    async def handle(self, message: str, context: RequestContext) -> str:
        text = (message or "").strip()
        actor = context.user_id or "slack-user"
        if not text and not context.files:
            return HELP
        if text.lower() in {"help", "?", "hi", "hello"}:
            return HELP

        try:
            confirm = CONFIRM_RE.match(text)
            if confirm:
                return self.receiving.confirm(confirm.group("receipt"), actor=actor)
            cancel = CANCEL_RE.match(text)
            if cancel:
                return self.receiving.cancel(cancel.group("receipt"), actor=actor)

            if context.files:
                receive = RECEIVE_RE.match(text)
                if not receive:
                    return (
                        "Attachment received. To stage it against a PO, use "
                        "`receive <PO-number> at <location>`."
                    )
                if len(context.files) != 1:
                    return "Please attach one delivery document at a time for receiving."
                path = await download_slack_file(context.files[0])
                receipt, preview = self.receiving.stage(
                    purchase_order_id=receive.group("po"),
                    source_path=path,
                    destination_location=receive.group("location"),
                    actor=actor,
                    supplier_delivery_note=context.files[0].get("name", ""),
                )
                logger.info(
                    "Staged inventory receipt %s request_id=%s",
                    receipt.receipt_id,
                    context.request_id,
                )
                return preview

            return self.commands.execute(text, actor=actor)
        except (InventoryDomainError, UploadValidationError) as exc:
            return f"Inventory rule prevented that operation: {exc}"
        except Exception:
            logger.exception("Inventory operation failed request_id=%s", context.request_id)
            return safe_error_message(context.request_id)

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
from src.inventory.po_intake import PurchaseOrderIntakeWorkflow
from src.knowledge.file_loader import UploadValidationError, download_slack_file

logger = logging.getLogger(__name__)

HELP = """*Inventory Agent*

*Storage locations*
Create a governed physical hierarchy before receiving/storing inventory:
• `create location SITE-A type site site SITE-A name Main Site`
• `create location STORE-A type storeroom site SITE-A name Main Store parent SITE-A`
• `create location SHELF-B3 type shelf site SITE-A name Shelf B3 parent STORE-A`
• `locations site SITE-A`
• `location path SHELF-B3`
• `deactivate location SHELF-B3`
• `activate location SHELF-B3`

*Purchase orders*
Attach a CSV/PDF/DOCX/text/image PO or quote and type:
• `create po PO-2026-0001 supplier Dell`
Then confirm or cancel the staged PO:
• `confirm po PO-STAGE-...`
• `cancel po PO-STAGE-...`
PO line columns: `line_id, sku, description, quantity, tracking_mode, unit_price, model`.

*Delivery receiving*
Attach a delivery note/photo and type:
• `receive PO-2026-0001 at STORE-A`
The destination must be an active governed inventory location.
Then use:
• `confirm receipt RCV-...`
• `cancel receipt RCV-...`

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
"""

RECEIVE_RE = re.compile(r"^receive\s+(?P<po>\S+)\s+at\s+(?P<location>\S+)$", re.I)
CONFIRM_RECEIPT_RE = re.compile(r"^confirm\s+receipt\s+(?P<receipt>\S+)$", re.I)
CANCEL_RECEIPT_RE = re.compile(r"^cancel\s+receipt\s+(?P<receipt>\S+)$", re.I)
CREATE_PO_RE = re.compile(r"^create\s+po\s+(?P<po>\S+)\s+supplier\s+(?P<supplier>.+)$", re.I)
CONFIRM_PO_RE = re.compile(r"^confirm\s+po\s+(?P<stage>\S+)$", re.I)
CANCEL_PO_RE = re.compile(r"^cancel\s+po\s+(?P<stage>\S+)$", re.I)


class InventoryAgent(BaseAgent):
    name = "inventory"

    def __init__(self):
        self.commands = InventoryCommandService()
        self.receiving = AssistedReceivingWorkflow()
        self.po_intake = PurchaseOrderIntakeWorkflow()

    async def handle(self, message: str, context: RequestContext) -> str:
        text = (message or "").strip()
        actor = context.user_id or "slack-user"
        if not text and not context.files:
            return HELP
        if text.lower() in {"help", "?", "hi", "hello"}:
            return HELP

        try:
            if match := CONFIRM_RECEIPT_RE.match(text):
                return self.receiving.confirm(match.group("receipt"), actor=actor)
            if match := CANCEL_RECEIPT_RE.match(text):
                return self.receiving.cancel(match.group("receipt"), actor=actor)
            if match := CONFIRM_PO_RE.match(text):
                return self.po_intake.confirm(match.group("stage"), actor=actor)
            if match := CANCEL_PO_RE.match(text):
                return self.po_intake.cancel(match.group("stage"), actor=actor)

            if context.files:
                if len(context.files) != 1:
                    return "Please attach one inventory document at a time."
                path = await download_slack_file(context.files[0])

                if match := CREATE_PO_RE.match(text):
                    staged, preview = self.po_intake.stage(
                        purchase_order_id=match.group("po"),
                        supplier=match.group("supplier").strip(),
                        source_path=path,
                        actor=actor,
                        external_reference=context.files[0].get("name", ""),
                    )
                    logger.info("Staged PO %s request_id=%s", staged.staging_id, context.request_id)
                    return preview

                if match := RECEIVE_RE.match(text):
                    receipt, preview = self.receiving.stage(
                        purchase_order_id=match.group("po"),
                        source_path=path,
                        destination_location=match.group("location"),
                        actor=actor,
                        supplier_delivery_note=context.files[0].get("name", ""),
                    )
                    logger.info(
                        "Staged inventory receipt %s request_id=%s",
                        receipt.receipt_id,
                        context.request_id,
                    )
                    return preview

                return (
                    "Attachment received. Use `create po <PO-number> supplier <supplier>` "
                    "or `receive <PO-number> at <location>`."
                )

            return self.commands.execute(text, actor=actor)
        except (InventoryDomainError, UploadValidationError) as exc:
            return f"Inventory rule prevented that operation: {exc}"
        except Exception:
            logger.exception("Inventory operation failed request_id=%s", context.request_id)
            return safe_error_message(context.request_id)

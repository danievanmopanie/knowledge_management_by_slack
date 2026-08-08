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
from src.inventory.overview import InventoryOverviewService
from src.inventory.po_intake import PurchaseOrderIntakeWorkflow
from src.inventory.stock_reconciliation import StockReconciliationWorkflow
from src.knowledge.file_loader import UploadValidationError, download_slack_file

logger = logging.getLogger(__name__)

HELP = """*Inventory Agent*

*Operations overview*
• `inventory summary`
• `overdue loans`

*Storage locations*
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
Then use `confirm po PO-STAGE-...` or `cancel po PO-STAGE-...`.
PO line columns: `line_id, sku, description, quantity, tracking_mode, unit_price, model`.

*Delivery receiving*
Attach a delivery note/photo and type:
• `receive PO-2026-0001 at STORE-A`
Then use `confirm receipt RCV-...` or `cancel receipt RCV-...`.

*Receiving exceptions*
• `exceptions`
• `exceptions po PO-2026-0001`
• `exceptions status resolved`
• `exception 42`
• `resolve exception 42 as supplier_replacement note Supplier will replace next week`
• `reopen exception 42 note Replacement did not arrive`
• `exception history 42`

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

*Physical stock counts*
`count stock` stages the variance before changing the ledger:
• `count stock MOUSE-01 at STORE-A = 97`
• `pending counts`
• `pending counts at STORE-A`
• `confirm count CNT-...`
• `cancel count CNT-...`
"""

RECEIVE_RE = re.compile(r"^receive\s+(?P<po>\S+)\s+at\s+(?P<location>\S+)$", re.I)
CONFIRM_RECEIPT_RE = re.compile(r"^confirm\s+receipt\s+(?P<receipt>\S+)$", re.I)
CANCEL_RECEIPT_RE = re.compile(r"^cancel\s+receipt\s+(?P<receipt>\S+)$", re.I)
CREATE_PO_RE = re.compile(r"^create\s+po\s+(?P<po>\S+)\s+supplier\s+(?P<supplier>.+)$", re.I)
CONFIRM_PO_RE = re.compile(r"^confirm\s+po\s+(?P<stage>\S+)$", re.I)
CANCEL_PO_RE = re.compile(r"^cancel\s+po\s+(?P<stage>\S+)$", re.I)
STAGE_COUNT_RE = re.compile(
    r"^count\s+stock\s+(?P<sku>\S+)\s+at\s+(?P<location>\S+)\s*=\s*(?P<count>\d+)$",
    re.I,
)
CONFIRM_COUNT_RE = re.compile(r"^confirm\s+count\s+(?P<count>\S+)$", re.I)
CANCEL_COUNT_RE = re.compile(r"^cancel\s+count\s+(?P<count>\S+)$", re.I)
PENDING_COUNTS_RE = re.compile(r"^pending\s+counts(?:\s+at\s+(?P<location>\S+))?$", re.I)


class InventoryAgent(BaseAgent):
    name = "inventory"

    def __init__(self):
        self.commands = InventoryCommandService()
        self.receiving = AssistedReceivingWorkflow()
        self.po_intake = PurchaseOrderIntakeWorkflow()
        self.stock_counts = StockReconciliationWorkflow(self.commands.repository)
        self.overview = InventoryOverviewService(self.commands.repository)

    async def handle(self, message: str, context: RequestContext) -> str:
        text = (message or "").strip()
        actor = context.user_id or "slack-user"
        if not text and not context.files:
            return HELP
        if text.lower() in {"help", "?", "hi", "hello"}:
            return HELP

        try:
            if text.lower() == "inventory summary":
                return self._inventory_summary()
            if text.lower() == "overdue loans":
                return self._overdue_loans()
            if match := CONFIRM_RECEIPT_RE.match(text):
                return self.receiving.confirm(match.group("receipt"), actor=actor)
            if match := CANCEL_RECEIPT_RE.match(text):
                return self.receiving.cancel(match.group("receipt"), actor=actor)
            if match := CONFIRM_PO_RE.match(text):
                return self.po_intake.confirm(match.group("stage"), actor=actor)
            if match := CANCEL_PO_RE.match(text):
                return self.po_intake.cancel(match.group("stage"), actor=actor)
            if match := CONFIRM_COUNT_RE.match(text):
                count = self.stock_counts.confirm(match.group("count"), actor=actor)
                return (
                    f"Confirmed stock count `{count.count_id}` for `{count.sku}` at "
                    f"`{count.location_id}`. Ledger adjusted from *{count.expected_on_hand}* "
                    f"to *{count.counted_quantity}* (variance *{count.variance:+d}*)."
                )
            if match := CANCEL_COUNT_RE.match(text):
                count = self.stock_counts.cancel(match.group("count"), actor=actor)
                return (
                    f"Cancelled stock count `{count.count_id}`. "
                    "The inventory ledger was not changed."
                )
            if match := PENDING_COUNTS_RE.match(text):
                location_id = match.group("location") or ""
                if location_id:
                    location_id = self.commands.locations.require_active(location_id).location_id
                rows = self.stock_counts.list_pending(location_id=location_id)
                if not rows:
                    return "No stock counts are awaiting confirmation."
                lines = ["*Pending stock counts*"]
                for item in rows[:50]:
                    lines.append(
                        f"• `{item.count_id}` — `{item.sku}` @ `{item.location_id}`: "
                        f"ledger {item.expected_on_hand}, counted {item.counted_quantity}, "
                        f"variance {item.variance:+d}"
                    )
                return "\n".join(lines)
            if match := STAGE_COUNT_RE.match(text):
                location = self.commands.locations.require_active(match.group("location"))
                staged = self.stock_counts.stage(
                    sku=match.group("sku"),
                    location_id=location.location_id,
                    counted_quantity=int(match.group("count")),
                    actor=actor,
                )
                return (
                    f"*Stock count staged* `{staged.count_id}`\n"
                    f"• SKU/location: `{staged.sku}` @ `{staged.location_id}`\n"
                    f"• Ledger on hand: *{staged.expected_on_hand}*\n"
                    f"• Reserved: *{staged.expected_reserved}*\n"
                    f"• Physical count: *{staged.counted_quantity}*\n"
                    f"• Variance: *{staged.variance:+d}*\n"
                    "No stock has changed. "
                    f"Use `confirm count {staged.count_id}` or `cancel count {staged.count_id}`."
                )

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

    def _inventory_summary(self) -> str:
        summary = self.overview.snapshot()
        return (
            "*Inventory operational summary*\n"
            f"• Active locations: *{summary.active_locations}*\n"
            f"• Open / partially received POs: *{summary.open_purchase_orders}*\n"
            f"• Serialized assets: *{summary.serialized_assets}* (issued: *{summary.issued_assets}*)\n"
            f"• Quantity stock on hand: *{summary.stock_units_on_hand}* "
            f"(reserved: *{summary.stock_units_reserved}*)\n"
            f"• Low-stock positions: *{summary.low_stock_positions}*\n"
            f"• Open receiving exceptions: *{summary.open_exceptions}*\n"
            f"• Overdue loans: *{summary.overdue_loans}* | Due in 7 days: *{summary.due_soon_loans}*\n"
            f"• Awaiting confirmation — POs: *{summary.pending_purchase_orders}*, "
            f"receipts: *{summary.pending_receipts}*, stock counts: *{summary.pending_stock_counts}*"
        )

    def _overdue_loans(self) -> str:
        rows = self.overview.overdue_loans()
        if not rows:
            return "No serialized loan assets are currently overdue."
        lines = ["*Overdue asset loans*"]
        for item in rows:
            lines.append(
                f"• `{item.asset_id}` / `{item.sku}` — `{item.assigned_to or '—'}` "
                f"customer `{item.customer_ref or '—'}` — due `{item.due_at.date().isoformat()}` "
                f"(*{item.days_overdue} day(s) overdue*)"
            )
        return "\n".join(lines)

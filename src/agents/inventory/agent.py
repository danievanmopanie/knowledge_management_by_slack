"""Inventory Management Agent – coordinator entry for #inventory."""

from __future__ import annotations

from typing import Any

from src.agents.base import BaseAgent

HELP = """*Inventory Agent*

Lifecycle is enforced (Ordered → Received → In Storeroom → Issued / Loan → Returned → …).

Examples:
• Attach an approved quote → bulk create order
• `deliver PO-2026-0001` + delivery-note photo
• Photo of barcode + `receive against PO-2026-0001`
• `store A-1042 shelf-B3`
• `issue A-1042 to @user dedicated`
• `issue A-1042 to @user loan until 2026-08-20`
• `return A-1042`
• `status A-1042` / `status PO-2026-0001`
• `escalate PO-2026-0001 – short shipped 2 units`

Barcode photos are decoded locally (pyzbar). Hardware scanners that type into Slack also work.
"""


class InventoryAgent(BaseAgent):
    """
    Entry point for #inventory.

    Full specialised sub-agents (Order, Intake, Storage, Issue, Returns,
    Compliance) will be wired via LangGraph; this handler is the Slack-facing
    coordinator stub until those graphs are connected.
    """

    name = "inventory"

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        text = (message or "").strip()
        files = context.get("files") or []

        if not text and not files:
            return HELP

        lower = text.lower()
        if lower in {"help", "?", "hi", "hello"}:
            return HELP

        # Lightweight acknowledgements until full graph is implemented
        if files and any(
            k in lower for k in ("quote", "order", "po", "deliver", "receive", "barcode")
        ):
            return (
                "Received your attachment for inventory processing.\n"
                "Order / intake / barcode pipelines are scaffolded "
                "(`src/inventory/`); full LangGraph wiring is next.\n\n"
                f"Message: {text or '(no text)'}"
            )

        if lower.startswith("status"):
            return (
                "Status lookup will query the local inventory store.\n"
                f"Requested: `{text}`"
            )

        return (
            "Inventory Agent received your request.\n"
            f"You said: {text}\n\n"
            "Type `help` for supported flows. Architecture: `docs/INVENTORY_ARCHITECTURE.md`."
        )

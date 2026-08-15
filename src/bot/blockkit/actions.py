"""Confirm/Cancel buttons for staged PO / receipt / stock-count replies.

`InventoryAgent.handle()` already returns a plain-text preview whenever it
stages a PO, receipt, or stock count. `attach_confirm_cancel()` recognises
those replies and attaches the repo-wide Block Kit decision controls so typed
commands remain a fallback rather than the primary UX.
"""

from __future__ import annotations

import re

from src.bot.blockkit import ids
from src.bot.blockkit.decisions import decision_actions, remove_action_blocks

_STAGE_PATTERNS: tuple[tuple[str, re.Pattern, str, str], ...] = (
    ("po", re.compile(r"confirm po (?P<id>[\w-]+)"), ids.CONFIRM_PO, ids.CANCEL_PO),
    ("receipt", re.compile(r"confirm receipt (?P<id>[\w-]+)"), ids.CONFIRM_RECEIPT, ids.CANCEL_RECEIPT),
    ("count", re.compile(r"confirm count (?P<id>[\w-]+)"), ids.CONFIRM_COUNT, ids.CANCEL_COUNT),
)

_CONFIRM_CANCEL_BLOCK_IDS = {
    "inventory_po_confirm_cancel",
    "inventory_receipt_confirm_cancel",
    "inventory_count_confirm_cancel",
}


def build_confirm_cancel_blocks(kind: str, staged_id: str) -> list[dict]:
    confirm_action, cancel_action = {
        "po": (ids.CONFIRM_PO, ids.CANCEL_PO),
        "receipt": (ids.CONFIRM_RECEIPT, ids.CANCEL_RECEIPT),
        "count": (ids.CONFIRM_COUNT, ids.CANCEL_COUNT),
    }[kind]
    return [
        decision_actions(
            block_id=f"inventory_{kind}_confirm_cancel",
            value=staged_id,
            primary_action_id=confirm_action,
            primary_label="Confirm",
            secondary_action_id=cancel_action,
            secondary_label="Cancel",
            secondary_style="danger",
        )
    ]


def attach_confirm_cancel(response_text: str) -> list[dict] | None:
    """Return the visible preview and Confirm/Cancel controls for staged replies."""
    for kind, pattern, _confirm_action, _cancel_action in _STAGE_PATTERNS:
        match = pattern.search(response_text)
        if match:
            return [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": response_text[:3000]},
                },
                *build_confirm_cancel_blocks(kind, match.group("id")),
            ]
    return None


def remove_confirm_cancel_blocks(blocks: list[dict]) -> list[dict]:
    """Remove only this feature's controls while preserving the visible preview."""
    return remove_action_blocks(blocks, *_CONFIRM_CANCEL_BLOCK_IDS)

"""Shared Block Kit primitives for human-in-the-loop decisions.

Repo-wide UX rule: when Slack is waiting for a human choice to continue a
workflow, present a Block Kit control first. Typed commands remain a fallback,
not the primary interaction.
"""

from __future__ import annotations

from typing import Any


def decision_actions(
    *,
    block_id: str,
    value: str,
    primary_action_id: str,
    primary_label: str,
    secondary_action_id: str | None = None,
    secondary_label: str | None = None,
    secondary_style: str | None = None,
) -> dict[str, Any]:
    """Build a consistent one-step decision row.

    Use the primary button for the action that advances the workflow and an
    optional secondary button for cancel/dismiss/back-out behavior.
    """
    elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": primary_label, "emoji": True},
            "style": "primary",
            "action_id": primary_action_id,
            "value": value,
        }
    ]
    if secondary_action_id and secondary_label:
        secondary: dict[str, Any] = {
            "type": "button",
            "text": {"type": "plain_text", "text": secondary_label, "emoji": True},
            "action_id": secondary_action_id,
            "value": value,
        }
        if secondary_style:
            secondary["style"] = secondary_style
        elements.append(secondary)
    return {"type": "actions", "block_id": block_id, "elements": elements}


def remove_action_blocks(blocks: list[dict], *block_ids: str) -> list[dict]:
    """Remove completed decision controls while leaving the visible card intact."""
    remove = set(block_ids)
    return [block for block in blocks if block.get("block_id") not in remove]

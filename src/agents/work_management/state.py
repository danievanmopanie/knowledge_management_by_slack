"""Graph state for the Work Management multi-agent graph."""

from __future__ import annotations

from typing import Any, TypedDict


class WorkManagementState(TypedDict, total=False):
    # Input
    message: str
    user: str | None
    channel_id: str | None
    thread_ts: str | None

    # Set by the orchestrator node
    intent: str
    work_item_id: str | None

    # Working data specialists can pass forward (kept small - the store is
    # the source of truth, this is just scratch space within one turn)
    scratch: dict[str, Any]

    # Output
    response: str

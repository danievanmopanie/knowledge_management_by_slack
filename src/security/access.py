"""Knowledge retrieval access policy."""

from __future__ import annotations

from typing import Any

from src.core.context import RequestContext


def _values(metadata: dict[str, Any], key: str) -> set[str]:
    value = metadata.get(key)
    if not value:
        return set()
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    return {str(item) for item in value}


def can_read(metadata: dict[str, Any], context: RequestContext | None) -> bool:
    """Deny restricted knowledge unless an explicit ACL matches the request."""
    visibility = str(metadata.get("visibility") or "internal").lower()
    if visibility in {"public", "internal"}:
        return True
    if visibility != "restricted" or context is None:
        return False

    if context.user_id and context.user_id in _values(metadata, "allowed_user_ids"):
        return True
    if context.channel_id and context.channel_id in _values(metadata, "allowed_channel_ids"):
        return True
    return False

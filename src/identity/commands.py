"""Shared Slack command for self-service identity linking (`link me <email>`)."""

from __future__ import annotations

import re

from src.core.context import RequestContext
from src.identity.resolver import IdentityResolver

_LINK_RE = re.compile(r"^link\s+me\s+(?P<email>[^@\s]+@[^@\s]+\.[^@\s]+)$", re.IGNORECASE)


def try_link_me(text: str, context: RequestContext, resolver: IdentityResolver) -> str | None:
    """Handle `link me <email>`; return a reply, or None if it isn't that command."""
    match = _LINK_RE.match((text or "").strip())
    if not match:
        return None
    if not context.user_id:
        return "I can't link an identity without a Slack user."
    email = match.group("email")
    resolver.register(context.user_id, email=email)
    return (
        f"Linked your Slack account to `{email}`. "
        "I'll use it to act as you in Snipe-IT and Taskwondo."
    )

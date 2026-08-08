"""Typed request context shared by Slack routing and agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RequestContext:
    """Identity and conversation metadata carried through one user request."""

    request_id: str = field(default_factory=lambda: uuid4().hex[:12])
    channel_id: str = ""
    user_id: str | None = None
    thread_ts: str | None = None
    files: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    roles: tuple[str, ...] = field(default_factory=tuple)
    groups: tuple[str, ...] = field(default_factory=tuple)
    site: str | None = None

    @classmethod
    def from_slack(
        cls,
        *,
        channel_id: str,
        user_id: str | None = None,
        thread_ts: str | None = None,
        files: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
        roles: list[str] | tuple[str, ...] | None = None,
        groups: list[str] | tuple[str, ...] | None = None,
        site: str | None = None,
    ) -> "RequestContext":
        kwargs: dict[str, Any] = {
            "channel_id": channel_id,
            "user_id": user_id,
            "thread_ts": thread_ts,
            "files": tuple(files or ()),
            "roles": tuple(roles or ()),
            "groups": tuple(groups or ()),
            "site": site,
        }
        if request_id:
            kwargs["request_id"] = request_id
        return cls(**kwargs)

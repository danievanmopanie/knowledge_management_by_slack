"""Slack bot package exports.

Keep package import side-effect free so standalone runtimes (for example
``src.bot.create_knowledge_app``) can map their dedicated environment variables
before the shared Settings object is instantiated.
"""

from __future__ import annotations

from typing import Any

__all__ = ["app", "start", "route_message"]


def __getattr__(name: str) -> Any:
    """Lazily preserve the historical ``src.bot`` package exports."""
    if name in {"app", "start"}:
        from .app import app, start

        return {"app": app, "start": start}[name]
    if name == "route_message":
        from .router import route_message

        return route_message
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

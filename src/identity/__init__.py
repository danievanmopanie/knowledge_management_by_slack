"""Slack ↔ external-system identity mapping.

Slack is the source of truth for *who* a person is; Snipe-IT and Taskwondo are
the systems of record for assets and work. This package maps a Slack user to
their accounts in those systems so agents (acting with a service-account token)
can stamp the real human on every action.
"""

from src.identity.resolver import (
    ExternalIdentity,
    IdentityResolutionError,
    IdentityResolver,
)
from src.identity.store import IdentityRecord, IdentityStore

__all__ = [
    "ExternalIdentity",
    "IdentityResolutionError",
    "IdentityResolver",
    "IdentityRecord",
    "IdentityStore",
]

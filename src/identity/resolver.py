"""Resolve a Slack user to their Snipe-IT / Taskwondo accounts.

Resolution order for each external system:
1. A mapping already stored for the Slack user.
2. Otherwise, if ``identity_auto_link_by_email`` is on and an email is known
   (from the mapping, the request context, or an explicit argument), look the
   user up by email in the external system and cache the result.

Callables for the external lookups are injected so the resolver stays unit
testable without network access; production wiring passes the real clients.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.core.config import settings
from src.core.context import RequestContext
from src.identity.store import IdentityStore

UserLookup = Callable[[str], dict | None]


class IdentityResolutionError(Exception):
    """Raised when a required external identity cannot be resolved."""


@dataclass(frozen=True)
class ExternalIdentity:
    """The resolved external identities for one Slack user."""

    slack_user_id: str
    email: str | None = None
    display_name: str | None = None
    snipeit_user_id: str | None = None
    taskwondo_user_id: str | None = None

    def stamp(self) -> str:
        """Human-readable attribution stamped onto external records."""
        who = self.display_name or self.email or self.slack_user_id
        return f"Requested by {who} (Slack {self.slack_user_id})"


class IdentityResolver:
    """Maps Slack users to external users, caching results in the identity store."""

    def __init__(
        self,
        store: IdentityStore | None = None,
        *,
        snipeit_lookup: UserLookup | None = None,
        taskwondo_lookup: UserLookup | None = None,
    ):
        self.store = store or IdentityStore()
        self._snipeit_lookup = snipeit_lookup
        self._taskwondo_lookup = taskwondo_lookup

    def _resolve_snipeit_lookup(self) -> UserLookup:
        if self._snipeit_lookup is not None:
            return self._snipeit_lookup
        from src.integrations import snipeit_client

        return snipeit_client.find_user_by_email

    def _resolve_taskwondo_lookup(self) -> UserLookup:
        if self._taskwondo_lookup is not None:
            return self._taskwondo_lookup
        from src.integrations import taskwondo_client

        return taskwondo_client.find_user_by_email

    def register(
        self,
        slack_user_id: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> ExternalIdentity:
        """Manually associate a Slack user with an email (the `link me` command)."""
        record = self.store.upsert(slack_user_id, email=email, display_name=display_name)
        return ExternalIdentity(
            slack_user_id=record.slack_user_id,
            email=record.email,
            display_name=record.display_name,
            snipeit_user_id=record.snipeit_user_id,
            taskwondo_user_id=record.taskwondo_user_id,
        )

    def resolve(
        self,
        context: RequestContext,
        *,
        want_snipeit: bool = False,
        want_taskwondo: bool = False,
    ) -> ExternalIdentity:
        slack_user_id = context.user_id or ""
        if not slack_user_id:
            raise IdentityResolutionError("No Slack user is associated with this request.")

        record = self.store.get(slack_user_id)
        email = (record.email if record else None) or context.email
        display_name = record.display_name if record else None
        snipeit_user_id = record.snipeit_user_id if record else None
        taskwondo_user_id = record.taskwondo_user_id if record else None

        # Persist a freshly-supplied email from the Slack context.
        if email and (record is None or record.email != email):
            self.store.upsert(slack_user_id, email=email)

        if want_snipeit and not snipeit_user_id:
            snipeit_user_id = self._link_external(
                slack_user_id, email, self._resolve_snipeit_lookup(), field="snipeit_user_id"
            )
            if not snipeit_user_id:
                raise IdentityResolutionError(self._unlinked_message(email, "Snipe-IT"))

        if want_taskwondo and not taskwondo_user_id:
            taskwondo_user_id = self._link_external(
                slack_user_id, email, self._resolve_taskwondo_lookup(), field="taskwondo_user_id"
            )
            if not taskwondo_user_id:
                raise IdentityResolutionError(self._unlinked_message(email, "Taskwondo"))

        # A lookup may have discovered a display name; reflect the latest stored value.
        if display_name is None:
            refreshed = self.store.get(slack_user_id)
            if refreshed is not None:
                display_name = refreshed.display_name

        return ExternalIdentity(
            slack_user_id=slack_user_id,
            email=email,
            display_name=display_name,
            snipeit_user_id=snipeit_user_id,
            taskwondo_user_id=taskwondo_user_id,
        )

    def _link_external(
        self,
        slack_user_id: str,
        email: str | None,
        lookup: UserLookup,
        *,
        field: str,
    ) -> str | None:
        if not email or not settings.identity_auto_link_by_email:
            return None
        user = lookup(email)
        if not user or user.get("id") in (None, ""):
            return None
        external_id = str(user["id"])
        display_name = user.get("name") or user.get("full_name")
        self.store.upsert(
            slack_user_id,
            display_name=display_name,
            **{field: external_id},
        )
        return external_id

    @staticmethod
    def _unlinked_message(email: str | None, system: str) -> str:
        if not email:
            return (
                f"I couldn't find your {system} account because I don't know your email yet. "
                "Register once with `link me your.name@company.com`."
            )
        return (
            f"I couldn't find a {system} account for `{email}`. "
            "Ask an admin to confirm the account exists, or link the correct address "
            "with `link me <email>`."
        )

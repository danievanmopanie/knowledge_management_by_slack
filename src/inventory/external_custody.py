"""Snipe-IT-backed asset custody for the Inventory agent.

When Snipe-IT is configured it becomes the system of record for hardware
custody: checkout/checkin/status run against Snipe-IT rather than the local
store. The agent uses a service-account token but resolves the Slack requester
to their Snipe-IT user and stamps the real human on every action.
"""

from __future__ import annotations

import logging
import re

from src.core.config import settings
from src.core.context import RequestContext
from src.identity.resolver import IdentityResolutionError, IdentityResolver
from src.integrations import snipeit_client

logger = logging.getLogger(__name__)

_CHECKOUT_RE = re.compile(r"^checkout\s+asset\s+(?P<tag>\S+)\s+to\s+(?P<target>\S+)$", re.IGNORECASE)
_CHECKIN_RE = re.compile(r"^check\s*in\s+asset\s+(?P<tag>\S+)$", re.IGNORECASE)
_STATUS_RE = re.compile(
    r"^(?:asset\s+status|where\s*is\s+asset)\s+(?P<tag>\S+)$", re.IGNORECASE
)


class SnipeITCustodyService:
    """Handles asset checkout/checkin/status commands against Snipe-IT."""

    def __init__(self, client=snipeit_client, resolver: IdentityResolver | None = None):
        self.client = client
        self.resolver = resolver or IdentityResolver()

    @property
    def enabled(self) -> bool:
        return bool((settings.snipeit_base_url or "").strip())

    def try_handle(self, text: str, context: RequestContext) -> str | None:
        """Return a reply for an asset-custody command, or None to fall through."""
        if not self.enabled:
            return None
        text = (text or "").strip()
        try:
            if match := _CHECKOUT_RE.match(text):
                return self._checkout(match.group("tag"), match.group("target"), context)
            if match := _CHECKIN_RE.match(text):
                return self._checkin(match.group("tag"), context)
            if match := _STATUS_RE.match(text):
                return self._status(match.group("tag"))
        except IdentityResolutionError as exc:
            return str(exc)
        except snipeit_client.SnipeITClientError as exc:
            logger.warning("Snipe-IT error request_id=%s: %s", context.request_id, exc)
            return f"Snipe-IT request failed: {exc}"
        return None

    def _resolve_asset(self, tag: str) -> dict:
        asset = self.client.find_hardware_by_tag(tag)
        if not asset or asset.get("id") in (None, ""):
            raise snipeit_client.SnipeITClientError(f"No Snipe-IT asset found for tag `{tag}`.")
        return asset

    def _checkout(self, tag: str, target: str, context: RequestContext) -> str:
        asset = self._resolve_asset(tag)

        if target.lower() == "me":
            # Requester and assignee are the same person; resolve once so the
            # Snipe-IT lookup also gives us their display name for the stamp.
            assignee = self.resolver.resolve(context, want_snipeit=True)
            assigned_user_id = assignee.snipeit_user_id
            assignee_label = assignee.display_name or assignee.email or "you"
            note = assignee.stamp()
        else:
            requester = self.resolver.resolve(context)  # attribution only
            user = self.client.find_user_by_email(target)
            if not user or user.get("id") in (None, ""):
                return f"I couldn't find a Snipe-IT user for `{target}`."
            assigned_user_id = str(user["id"])
            assignee_label = user.get("name") or target
            note = f"{requester.stamp()} on behalf of {target}"

        self.client.checkout_hardware(asset["id"], assigned_user_id=assigned_user_id, note=note)
        link = self._deep_link(asset["id"])
        suffix = f"\n{link}" if link else ""
        return f"Checked out `{tag}` to *{assignee_label}* in Snipe-IT.{suffix}"

    def _checkin(self, tag: str, context: RequestContext) -> str:
        asset = self._resolve_asset(tag)
        requester = self.resolver.resolve(context)
        self.client.checkin_hardware(asset["id"], note=requester.stamp())
        link = self._deep_link(asset["id"])
        suffix = f"\n{link}" if link else ""
        return f"Checked in `{tag}` in Snipe-IT.{suffix}"

    def _status(self, tag: str) -> str:
        asset = self._resolve_asset(tag)
        status_label = asset.get("status_label") or {}
        status = status_label.get("name") if isinstance(status_label, dict) else status_label
        assigned = asset.get("assigned_to") or {}
        if isinstance(assigned, dict):
            assignee = assigned.get("name") or assigned.get("username")
        else:
            assignee = assigned
        link = self._deep_link(asset["id"])
        suffix = f"\n{link}" if link else ""
        return (
            f"*Asset `{tag}`* (Snipe-IT)\n"
            f"• Status: `{status or 'unknown'}`\n"
            f"• Assigned to: `{assignee or 'unassigned'}`{suffix}"
        )

    @staticmethod
    def _deep_link(asset_id) -> str:
        base = (settings.snipeit_deep_link_base or settings.snipeit_base_url or "").strip().rstrip("/")
        if not base or asset_id in (None, ""):
            return ""
        return f"{base}/hardware/{asset_id}"

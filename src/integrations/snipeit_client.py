"""Minimal Snipe-IT REST client.

Snipe-IT is the external system of record for hardware assets. Agents call it
with a single service-account token and stamp the real human requester on every
action (checkout ``assigned_user`` + a "Requested by ..." note).

Snipe-IT quirk: mutating endpoints (checkout/checkin) return HTTP 200 even on
logical failure, signalling the outcome only through a ``status`` field in the
JSON body. ``_ensure_payload_ok`` therefore inspects the body rather than trusting
the HTTP status alone.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0


class SnipeITClientError(Exception):
    """Raised when a Snipe-IT operation fails."""


def _base_url() -> str:
    base = (settings.snipeit_base_url or "").strip().rstrip("/")
    if not base:
        raise SnipeITClientError("SNIPEIT_BASE_URL is not configured.")
    return f"{base}/api/v1"


def _headers() -> dict[str, str]:
    if not settings.snipeit_api_token:
        raise SnipeITClientError("SNIPEIT_API_TOKEN is not configured.")
    return {
        "Authorization": f"Bearer {settings.snipeit_api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> Any:
    url = f"{_base_url()}{path}"
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.request(method, url, headers=_headers(), params=params, json=json)
    if response.status_code >= 400:
        logger.error("Snipe-IT %s %s failed status=%s body=%s", method, path, response.status_code, response.text)
        raise SnipeITClientError(f"Snipe-IT API error {response.status_code}: {response.text[:500]}")
    try:
        return response.json()
    except ValueError as exc:
        raise SnipeITClientError(f"Snipe-IT returned a non-JSON response for {path}.") from exc


def _ensure_payload_ok(payload: Any, action: str) -> dict[str, Any]:
    """Snipe-IT reports mutation failures in the body with status='error'."""
    if isinstance(payload, dict) and payload.get("status") == "error":
        messages = payload.get("messages")
        raise SnipeITClientError(f"Snipe-IT {action} failed: {messages}")
    return payload if isinstance(payload, dict) else {}


def find_user_by_email(email: str) -> dict[str, Any] | None:
    """Return the Snipe-IT user whose email matches (case-insensitive), or None."""
    email = (email or "").strip()
    if not email:
        return None
    data = _request("GET", "/users", params={"search": email, "limit": 50})
    rows = data.get("rows", []) if isinstance(data, dict) else []
    for row in rows:
        if str(row.get("email", "")).strip().lower() == email.lower():
            return row
    return None


def get_hardware(asset_id: str | int) -> dict[str, Any]:
    """Fetch a single hardware asset by its Snipe-IT numeric id."""
    return _request("GET", f"/hardware/{str(asset_id).strip()}")


def find_hardware_by_tag(asset_tag: str) -> dict[str, Any] | None:
    """Resolve a human-facing asset tag (e.g. A-1042) to its hardware record."""
    asset_tag = (asset_tag or "").strip()
    if not asset_tag:
        return None
    data = _request("GET", "/hardware/bytag/" + asset_tag)
    if isinstance(data, dict) and data.get("status") == "error":
        # Fall back to a search when the direct bytag lookup is unavailable.
        search = _request("GET", "/hardware", params={"search": asset_tag, "limit": 50})
        rows = search.get("rows", []) if isinstance(search, dict) else []
        for row in rows:
            if str(row.get("asset_tag", "")).strip().lower() == asset_tag.lower():
                return row
        return None
    return data if isinstance(data, dict) else None


def checkout_hardware(
    asset_id: str | int,
    *,
    assigned_user_id: str | int,
    note: str = "",
) -> dict[str, Any]:
    """Check an asset out to a Snipe-IT user; ``note`` stamps the real requester."""
    payload = _request(
        "POST",
        f"/hardware/{str(asset_id).strip()}/checkout",
        json={
            "checkout_to_type": "user",
            "assigned_user": assigned_user_id,
            "note": note,
        },
    )
    return _ensure_payload_ok(payload, "checkout")


def checkin_hardware(asset_id: str | int, *, note: str = "") -> dict[str, Any]:
    """Check an asset back in."""
    payload = _request(
        "POST",
        f"/hardware/{str(asset_id).strip()}/checkin",
        json={"note": note},
    )
    return _ensure_payload_ok(payload, "checkin")

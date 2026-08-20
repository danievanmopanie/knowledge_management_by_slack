"""Minimal Taskwondo REST client.

Taskwondo (https://github.com/marcoshack/taskwondo) is the external system of
record for work items / tasks. It exposes a Go REST API under ``/api/v1`` secured
with ``twk_``-prefixed API keys. Agents call it with a single service-account key
and set the ``assignee_id`` to the mapped human so techs see their own name.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0


class TaskwondoClientError(Exception):
    """Raised when a Taskwondo operation fails."""


def _base_url() -> str:
    base = (settings.taskwondo_base_url or "").strip().rstrip("/")
    if not base:
        raise TaskwondoClientError("TASKWONDO_BASE_URL is not configured.")
    return f"{base}/api/v1"


def _headers() -> dict[str, str]:
    if not settings.taskwondo_api_token:
        raise TaskwondoClientError("TASKWONDO_API_TOKEN is not configured.")
    return {
        "Authorization": f"Bearer {settings.taskwondo_api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> Any:
    url = f"{_base_url()}{path}"
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.request(method, url, headers=_headers(), params=params, json=json)
    if response.status_code >= 400:
        logger.error(
            "Taskwondo %s %s failed status=%s body=%s", method, path, response.status_code, response.text
        )
        raise TaskwondoClientError(f"Taskwondo API error {response.status_code}: {response.text[:500]}")
    if response.status_code == 204 or not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise TaskwondoClientError(f"Taskwondo returned a non-JSON response for {path}.") from exc


def _extract_list(data: Any) -> list[dict[str, Any]]:
    """Taskwondo list endpoints may wrap rows under items/data/rows/results."""
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("items", "data", "rows", "results", "work_items", "users"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def find_user_by_email(email: str) -> dict[str, Any] | None:
    """Return the Taskwondo user whose email matches (case-insensitive), or None."""
    email = (email or "").strip()
    if not email:
        return None
    data = _request("GET", "/users", params={"search": email, "limit": 50})
    for row in _extract_list(data):
        if str(row.get("email", "")).strip().lower() == email.lower():
            return row
    return None


def create_work_item(
    *,
    title: str,
    item_type: str = "task",
    assignee_id: str | int | None = None,
    project: str | None = None,
    description: str = "",
    priority: str | None = None,
) -> dict[str, Any]:
    """Create a work item. ``project`` accepts a Taskwondo project key (e.g. OPS)."""
    if not (title or "").strip():
        raise TaskwondoClientError("A work item title is required.")
    body: dict[str, Any] = {"title": title.strip(), "type": item_type}
    project_key = (project or settings.taskwondo_default_project or "").strip()
    if project_key:
        body["project"] = project_key
    if assignee_id is not None and str(assignee_id).strip():
        body["assignee_id"] = assignee_id
    if description:
        body["description"] = description
    if priority:
        body["priority"] = priority
    data = _request("POST", "/work-items", json=body)
    return data if isinstance(data, dict) else {}


def get_work_item(work_item_id: str | int) -> dict[str, Any]:
    """Fetch a single work item by internal id or display id (PROJ-1)."""
    data = _request("GET", f"/work-items/{str(work_item_id).strip()}")
    return data if isinstance(data, dict) else {}


def list_work_items(
    *,
    assignee_id: str | int | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List work items, optionally filtered by assignee and status."""
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
    if assignee_id is not None and str(assignee_id).strip():
        params["assignee"] = assignee_id
    if status:
        params["status"] = status
    data = _request("GET", "/work-items", params=params)
    return _extract_list(data)


def update_work_item(
    work_item_id: str | int,
    *,
    status: str | None = None,
    assignee_id: str | int | None = None,
) -> dict[str, Any]:
    """Patch a work item's status and/or assignee."""
    body: dict[str, Any] = {}
    if status:
        body["status"] = status
    if assignee_id is not None and str(assignee_id).strip():
        body["assignee_id"] = assignee_id
    if not body:
        raise TaskwondoClientError("update_work_item requires a status or assignee_id.")
    data = _request("PATCH", f"/work-items/{str(work_item_id).strip()}", json=body)
    return data if isinstance(data, dict) else {}

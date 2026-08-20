"""Tests for the minimal Taskwondo REST client."""

import json

import httpx
import pytest

from src.core.config import settings
from src.integrations.taskwondo_client import (
    TaskwondoClientError,
    create_work_item,
    find_user_by_email,
    list_work_items,
    update_work_item,
)

_RealClient = httpx.Client


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "taskwondo_base_url", "https://taskwondo.tailnet")
    monkeypatch.setattr(settings, "taskwondo_api_token", "twk_test")
    monkeypatch.setattr(settings, "taskwondo_default_project", "OPS")


def _mock_client(handler):
    return lambda *args, **kwargs: _RealClient(
        transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")
    )


def test_create_work_item_sends_expected_payload(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "wi_1", "display_id": "OPS-7", "title": "Fix laptop"})

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))

    result = create_work_item(
        title="Fix laptop",
        item_type="task",
        assignee_id="user_9",
        description="Requested by Jane (Slack U1)",
    )

    assert result["display_id"] == "OPS-7"
    assert captured["url"] == "https://taskwondo.tailnet/api/v1/work-items"
    assert captured["auth"] == "Bearer twk_test"
    assert captured["body"] == {
        "title": "Fix laptop",
        "type": "task",
        "project": "OPS",
        "assignee_id": "user_9",
        "description": "Requested by Jane (Slack U1)",
    }


def test_create_work_item_requires_title(monkeypatch):
    _configure(monkeypatch)
    with pytest.raises(TaskwondoClientError):
        create_work_item(title="   ")


def test_find_user_by_email_unwraps_items_and_matches(monkeypatch):
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"id": "user_9", "email": "jane@company.com", "name": "Jane"}]},
        )

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))
    user = find_user_by_email("JANE@company.com")
    assert user == {"id": "user_9", "email": "jane@company.com", "name": "Jane"}


def test_list_work_items_filters_by_assignee(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"items": [{"id": "wi_1", "display_id": "OPS-7"}]})

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))
    rows = list_work_items(assignee_id="user_9", status="open", limit=10)
    assert rows == [{"id": "wi_1", "display_id": "OPS-7"}]
    assert "assignee=user_9" in captured["url"]
    assert "status=open" in captured["url"]


def test_update_work_item_patches_status(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "wi_1", "status": "in_progress"})

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))
    result = update_work_item("OPS-7", status="in_progress")
    assert result["status"] == "in_progress"
    assert captured["method"] == "PATCH"
    assert captured["url"] == "https://taskwondo.tailnet/api/v1/work-items/OPS-7"
    assert captured["body"] == {"status": "in_progress"}


def test_update_work_item_requires_a_field(monkeypatch):
    _configure(monkeypatch)
    with pytest.raises(TaskwondoClientError):
        update_work_item("OPS-7")


def test_requires_configuration(monkeypatch):
    monkeypatch.setattr(settings, "taskwondo_base_url", "")
    monkeypatch.setattr(settings, "taskwondo_api_token", "twk_test")
    with pytest.raises(TaskwondoClientError):
        list_work_items()

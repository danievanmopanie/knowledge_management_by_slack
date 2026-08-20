"""Tests for the minimal Snipe-IT REST client."""

import httpx
import pytest

from src.core.config import settings
from src.integrations import snipeit_client
from src.integrations.snipeit_client import (
    SnipeITClientError,
    checkin_hardware,
    checkout_hardware,
    find_user_by_email,
)

_RealClient = httpx.Client


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "snipeit_base_url", "https://snipeit.tailnet/")
    monkeypatch.setattr(settings, "snipeit_api_token", "snipe-token")


def _mock_client(handler):
    return lambda *args, **kwargs: _RealClient(
        transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")
    )


def test_find_user_by_email_matches_exact_address(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "total": 2,
                "rows": [
                    {"id": 5, "email": "other@company.com", "name": "Other"},
                    {"id": 9, "email": "Jane@Company.com", "name": "Jane Smith"},
                ],
            },
        )

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))

    user = find_user_by_email("jane@company.com")

    assert user == {"id": 9, "email": "Jane@Company.com", "name": "Jane Smith"}
    assert captured["url"].startswith("https://snipeit.tailnet/api/v1/users?")
    assert captured["auth"] == "Bearer snipe-token"


def test_find_user_by_email_returns_none_when_absent(monkeypatch):
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0, "rows": []})

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))
    assert find_user_by_email("ghost@company.com") is None


def test_checkout_hardware_success_sends_expected_payload(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["url"] = str(request.url)
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"status": "success", "messages": "Asset checked out."})

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))

    result = checkout_hardware(42, assigned_user_id=9, note="Requested by Jane (Slack U1)")

    assert result["status"] == "success"
    assert captured["url"] == "https://snipeit.tailnet/api/v1/hardware/42/checkout"
    assert captured["body"] == {
        "checkout_to_type": "user",
        "assigned_user": 9,
        "note": "Requested by Jane (Slack U1)",
    }


def test_checkout_hardware_raises_on_status_error_body(monkeypatch):
    """Snipe-IT returns HTTP 200 with status='error' when checkout is refused."""
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "error", "messages": {"asset": "already checked out"}}
        )

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))

    with pytest.raises(SnipeITClientError):
        checkout_hardware(42, assigned_user_id=9, note="x")


def test_checkin_hardware_posts_note(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["url"] = str(request.url)
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"status": "success"})

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))

    checkin_hardware(42, note="returned by tech")

    assert captured["url"] == "https://snipeit.tailnet/api/v1/hardware/42/checkin"
    assert captured["body"] == {"note": "returned by tech"}


def test_requires_base_url(monkeypatch):
    monkeypatch.setattr(settings, "snipeit_base_url", "")
    monkeypatch.setattr(settings, "snipeit_api_token", "t")
    with pytest.raises(SnipeITClientError):
        find_user_by_email("jane@company.com")


def test_requires_token(monkeypatch):
    monkeypatch.setattr(settings, "snipeit_base_url", "https://snipeit.tailnet")
    monkeypatch.setattr(settings, "snipeit_api_token", "")
    with pytest.raises(SnipeITClientError):
        find_user_by_email("jane@company.com")


def test_http_error_raises(monkeypatch):
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))
    with pytest.raises(SnipeITClientError):
        snipeit_client.get_hardware(1)

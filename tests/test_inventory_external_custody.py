"""Tests for Snipe-IT-backed asset custody in the Inventory agent."""

import asyncio
from types import SimpleNamespace

import pytest

from src.agents.inventory.agent import InventoryAgent
from src.core.config import settings
from src.core.context import RequestContext
from src.identity.resolver import IdentityResolver
from src.identity.store import IdentityStore
from src.integrations.snipeit_client import SnipeITClientError
from src.inventory.external_custody import SnipeITCustodyService


@pytest.fixture(autouse=True)
def _snipeit_configured(monkeypatch):
    monkeypatch.setattr(settings, "snipeit_base_url", "https://snipeit.tailnet")
    monkeypatch.setattr(settings, "snipeit_api_token", "snipe-token")


def _resolver(tmp_path, *, snipeit_lookup=None):
    store = IdentityStore(path=tmp_path / "platform.db")
    return IdentityResolver(
        store,
        snipeit_lookup=snipeit_lookup or (lambda email: {"id": 9, "name": "Jane Smith"}),
    )


def _fake_client(**overrides):
    client = SimpleNamespace(
        SnipeITClientError=SnipeITClientError,
        find_hardware_by_tag=lambda tag: {"id": 42, "asset_tag": tag},
        find_user_by_email=lambda email: {"id": 15, "name": "Bob"},
        checkout_hardware=lambda *a, **kw: {"status": "success"},
        checkin_hardware=lambda *a, **kw: {"status": "success"},
    )
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


def _ctx(email="jane@company.com"):
    return RequestContext.from_slack(channel_id="C1", user_id="U1", email=email)


def test_checkout_to_me_stamps_requester_and_assigns_self(tmp_path):
    captured = {}

    def checkout_hardware(asset_id, *, assigned_user_id, note):
        captured.update(asset_id=asset_id, assigned_user_id=assigned_user_id, note=note)
        return {"status": "success"}

    service = SnipeITCustodyService(
        client=_fake_client(checkout_hardware=checkout_hardware),
        resolver=_resolver(tmp_path),
    )
    reply = service.try_handle("checkout asset A-1042 to me", _ctx())

    assert captured["asset_id"] == 42
    assert captured["assigned_user_id"] == "9"
    assert captured["note"] == "Requested by Jane Smith (Slack U1)"
    assert "Checked out `A-1042`" in reply
    assert "https://snipeit.tailnet/hardware/42" in reply


def test_checkout_to_email_assigns_other_and_notes_requester(tmp_path):
    captured = {}

    def checkout_hardware(asset_id, *, assigned_user_id, note):
        captured.update(assigned_user_id=assigned_user_id, note=note)
        return {"status": "success"}

    service = SnipeITCustodyService(
        client=_fake_client(
            checkout_hardware=checkout_hardware,
            find_user_by_email=lambda email: {"id": 15, "name": "Bob Jones"},
        ),
        resolver=_resolver(tmp_path),
    )
    reply = service.try_handle("checkout asset A-1042 to bob@company.com", _ctx())

    assert captured["assigned_user_id"] == "15"
    assert "on behalf of bob@company.com" in captured["note"]
    assert "Bob Jones" in reply


def test_checkin_calls_snipeit(tmp_path):
    captured = {}

    def checkin_hardware(asset_id, *, note):
        captured.update(asset_id=asset_id, note=note)
        return {"status": "success"}

    service = SnipeITCustodyService(
        client=_fake_client(checkin_hardware=checkin_hardware),
        resolver=_resolver(tmp_path),
    )
    reply = service.try_handle("checkin asset A-1042", _ctx())
    assert captured["asset_id"] == 42
    assert "Checked in `A-1042`" in reply


def test_status_reports_assignment(tmp_path):
    service = SnipeITCustodyService(
        client=_fake_client(
            find_hardware_by_tag=lambda tag: {
                "id": 42,
                "asset_tag": tag,
                "status_label": {"name": "Deployed"},
                "assigned_to": {"name": "Jane Smith"},
            }
        ),
        resolver=_resolver(tmp_path),
    )
    reply = service.try_handle("asset status A-1042", _ctx())
    assert "Deployed" in reply and "Jane Smith" in reply


def test_unknown_asset_reports_error(tmp_path):
    service = SnipeITCustodyService(
        client=_fake_client(find_hardware_by_tag=lambda tag: None),
        resolver=_resolver(tmp_path),
    )
    reply = service.try_handle("checkin asset NOPE", _ctx())
    assert "No Snipe-IT asset found" in reply


def test_disabled_when_snipeit_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "snipeit_base_url", "")
    service = SnipeITCustodyService(client=_fake_client(), resolver=_resolver(tmp_path))
    assert service.try_handle("checkout asset A-1042 to me", _ctx()) is None


def test_non_custody_command_falls_through(tmp_path):
    service = SnipeITCustodyService(client=_fake_client(), resolver=_resolver(tmp_path))
    assert service.try_handle("inventory summary", _ctx()) is None


def test_inventory_agent_routes_checkout_to_snipeit(tmp_path):
    captured = {}

    def checkout_hardware(asset_id, *, assigned_user_id, note):
        captured.update(asset_id=asset_id, assigned_user_id=assigned_user_id)
        return {"status": "success"}

    custody = SnipeITCustodyService(
        client=_fake_client(checkout_hardware=checkout_hardware),
        resolver=_resolver(tmp_path),
    )
    agent = InventoryAgent(custody=custody)
    reply = asyncio.run(agent.handle("checkout asset A-1042 to me", _ctx()))
    assert captured["asset_id"] == 42
    assert "Checked out `A-1042`" in reply


def test_inventory_agent_local_commands_unaffected_when_snipeit_off(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "snipeit_base_url", "")
    agent = InventoryAgent()
    reply = asyncio.run(agent.handle("help", _ctx()))
    assert "Inventory Agent" in reply

"""Tests for the Taskwondo-backed Work Management agent."""

import asyncio
from types import SimpleNamespace

import pytest

from src.agents.work_management.agent import WorkManagementAgent
from src.core.config import settings
from src.core.context import RequestContext
from src.identity.resolver import IdentityResolver
from src.identity.store import IdentityStore
from src.integrations.taskwondo_client import TaskwondoClientError


@pytest.fixture()
def resolver(tmp_path):
    store = IdentityStore(path=tmp_path / "platform.db")
    return IdentityResolver(store, taskwondo_lookup=lambda email: {"id": "user_9", "name": "Jane"})


def _fake_client(**overrides):
    client = SimpleNamespace(
        TaskwondoClientError=TaskwondoClientError,
        create_work_item=lambda **kw: {"display_id": "OPS-7", "id": "wi_1", "title": kw["title"]},
        list_work_items=lambda **kw: [],
        update_work_item=lambda *a, **kw: {"id": a[0]},
    )
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


def _ctx(email="jane@company.com"):
    return RequestContext.from_slack(channel_id="C1", user_id="U1", email=email)


def _run(agent, text, ctx):
    return asyncio.run(agent.handle(text, ctx))


def test_create_task_assigns_to_resolved_human(resolver, monkeypatch):
    monkeypatch.setattr(settings, "taskwondo_base_url", "https://taskwondo.tailnet")
    captured = {}

    def create_work_item(**kwargs):
        captured.update(kwargs)
        return {"display_id": "OPS-7", "id": "wi_1"}

    agent = WorkManagementAgent(client=_fake_client(create_work_item=create_work_item), resolver=resolver)
    reply = _run(agent, "create task Replace docking station", _ctx())

    assert captured["assignee_id"] == "user_9"
    assert captured["item_type"] == "task"
    assert captured["description"] == "Requested by Jane (Slack U1)"
    assert "OPS-7" in reply
    assert "https://taskwondo.tailnet/work-items/OPS-7" in reply


def test_create_task_with_explicit_project(resolver):
    captured = {}

    def create_work_item(**kwargs):
        captured.update(kwargs)
        return {"display_id": "PROJ-1"}

    agent = WorkManagementAgent(client=_fake_client(create_work_item=create_work_item), resolver=resolver)
    _run(agent, "create bug Login screen crashes in PROJ", _ctx())

    assert captured["project"] == "PROJ"
    assert captured["item_type"] == "bug"
    assert captured["title"] == "Login screen crashes"


def test_my_tasks_lists_assigned_items(resolver):
    rows = [{"display_id": "OPS-7", "title": "Fix laptop", "status": "open"}]
    agent = WorkManagementAgent(
        client=_fake_client(list_work_items=lambda **kw: rows), resolver=resolver
    )
    reply = _run(agent, "my tasks", _ctx())
    assert "OPS-7" in reply and "Fix laptop" in reply and "[open]" in reply


def test_update_status_normalizes_and_patches(resolver):
    captured = {}

    def update_work_item(work_item_id, **kwargs):
        captured["id"] = work_item_id
        captured.update(kwargs)
        return {"id": work_item_id}

    agent = WorkManagementAgent(
        client=_fake_client(update_work_item=update_work_item), resolver=resolver
    )
    reply = _run(agent, "task OPS-7 status In Progress", _ctx())
    assert captured == {"id": "OPS-7", "status": "in_progress"}
    assert "in_progress" in reply


def test_unlinked_user_is_prompted_to_link(tmp_path):
    store = IdentityStore(path=tmp_path / "platform.db")
    resolver = IdentityResolver(store, taskwondo_lookup=lambda email: None)
    agent = WorkManagementAgent(client=_fake_client(), resolver=resolver)
    reply = _run(agent, "create task Do a thing", _ctx(email=None))
    assert "link me" in reply


def test_link_me_registers_email(resolver):
    agent = WorkManagementAgent(client=_fake_client(), resolver=resolver)
    reply = _run(agent, "link me jane@company.com", _ctx(email=None))
    assert "jane@company.com" in reply


def test_client_error_is_reported(resolver):
    def boom(**kwargs):
        raise TaskwondoClientError("project not found")

    agent = WorkManagementAgent(client=_fake_client(create_work_item=boom), resolver=resolver)
    reply = _run(agent, "create task Do a thing", _ctx())
    assert "Taskwondo request failed" in reply


def test_help_when_empty(resolver):
    agent = WorkManagementAgent(client=_fake_client(), resolver=resolver)
    assert "Work Management" in _run(agent, "", _ctx())

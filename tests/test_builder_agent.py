"""Tests for the Slack-facing natural-language Builder Agent."""

import asyncio

from src.agents.builder.agent import BuilderAgent
from src.core.config import settings
from src.core.context import RequestContext


class FakeBuilderTaskStore:
    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.enqueue_calls: list[dict] = []

    def enqueue(self, *, goal, requester_id, channel_id, thread_ts):
        task_id = f"bld_{len(self.tasks) + 1}"
        self.enqueue_calls.append(
            {"goal": goal, "requester_id": requester_id, "channel_id": channel_id, "thread_ts": thread_ts}
        )
        self.tasks[task_id] = {
            "task_id": task_id,
            "goal": goal,
            "status": "pending",
            "branch_name": None,
            "pr_url": None,
            "error_message": None,
        }
        return task_id

    def get(self, task_id):
        return self.tasks.get(task_id)

    def mark_cancelled(self, task_id):
        task = self.tasks.get(task_id)
        if task and task["status"] == "pending":
            task["status"] = "cancelled"
            return True
        return False


def _agent(monkeypatch, allowed_user_ids: str = "U1"):
    monkeypatch.setattr(settings, "builder_agent_allowed_user_ids", allowed_user_ids)
    agent = BuilderAgent.__new__(BuilderAgent)
    agent.tasks = FakeBuilderTaskStore()
    return agent


def _context(user_id: str = "U1") -> RequestContext:
    return RequestContext.from_slack(channel_id="C1", user_id=user_id, thread_ts="123.45")


def test_help_explains_natural_language(monkeypatch):
    agent = _agent(monkeypatch)
    result = asyncio.run(agent.handle("", _context()))
    assert "Talk to me naturally" in result
    assert "No `build:` prefix is required" in result


def test_disallowed_user_is_refused(monkeypatch):
    agent = _agent(monkeypatch, allowed_user_ids="U2")
    result = asyncio.run(agent.handle("Please add a health check", _context(user_id="U1")))
    assert "not on the Builder Agent allowlist" in result
    assert agent.tasks.enqueue_calls == []


def test_natural_request_enqueues_turn_without_magic_prefix(monkeypatch):
    agent = _agent(monkeypatch)
    result = asyncio.run(agent.handle("Please add a health check endpoint", _context()))

    assert len(agent.tasks.enqueue_calls) == 1
    call = agent.tasks.enqueue_calls[0]
    assert "Please add a health check endpoint" in call["goal"]
    assert "never require magic command words" in call["goal"]
    assert call["requester_id"] == "U1"
    assert call["channel_id"] == "C1"
    assert call["thread_ts"] == "123.45"
    assert "bld_1" in result


def test_legacy_build_prefix_remains_backward_compatible(monkeypatch):
    agent = _agent(monkeypatch)
    asyncio.run(agent.handle("build: add a health check endpoint", _context()))
    call = agent.tasks.enqueue_calls[0]
    assert "add a health check endpoint" in call["goal"]
    assert "build: add a health check endpoint" not in call["goal"]


def test_status_command_reports_task_state(monkeypatch):
    agent = _agent(monkeypatch)
    ack = asyncio.run(agent.handle("Do a thing", _context()))
    task_id = ack.split("`")[1]
    result = asyncio.run(agent.handle(f"status {task_id}", _context()))
    assert task_id in result
    assert "pending" in result


def test_status_command_unknown_task(monkeypatch):
    agent = _agent(monkeypatch)
    result = asyncio.run(agent.handle("status bld_missing", _context()))
    assert "No Builder turn found" in result


def test_cancel_command_cancels_pending_task(monkeypatch):
    agent = _agent(monkeypatch)
    ack = asyncio.run(agent.handle("Do a thing", _context()))
    task_id = ack.split("`")[1]
    result = asyncio.run(agent.handle(f"cancel {task_id}", _context()))
    assert "Cancelled Builder turn" in result
    assert agent.tasks.get(task_id)["status"] == "cancelled"

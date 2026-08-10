"""Tests for the Slack-facing Builder Agent (enqueue-only, no Aider execution)."""

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
    # Bypass __init__ so no real BuilderTaskStore/sqlite file is created for this test.
    agent = BuilderAgent.__new__(BuilderAgent)
    agent.tasks = FakeBuilderTaskStore()
    return agent


def _context(user_id: str = "U1") -> RequestContext:
    return RequestContext.from_slack(channel_id="C1", user_id=user_id, thread_ts="123.45")


def test_help_text_for_empty_message(monkeypatch):
    agent = _agent(monkeypatch)

    result = asyncio.run(agent.handle("", _context()))

    assert "Builder Agent" in result


def test_disallowed_user_is_refused(monkeypatch):
    agent = _agent(monkeypatch, allowed_user_ids="U2")

    result = asyncio.run(agent.handle("build: add a feature", _context(user_id="U1")))

    assert "not on the Builder Agent allowlist" in result
    assert agent.tasks.enqueue_calls == []


def test_build_command_enqueues_task(monkeypatch):
    agent = _agent(monkeypatch)

    result = asyncio.run(agent.handle("build: add a health check endpoint", _context()))

    assert len(agent.tasks.enqueue_calls) == 1
    call = agent.tasks.enqueue_calls[0]
    assert call["goal"] == "add a health check endpoint"
    assert call["requester_id"] == "U1"
    assert call["channel_id"] == "C1"
    assert call["thread_ts"] == "123.45"
    assert "bld_1" in result


def test_build_command_without_goal_is_rejected(monkeypatch):
    agent = _agent(monkeypatch)

    result = asyncio.run(agent.handle("build:", _context()))

    assert "describe the change" in result
    assert agent.tasks.enqueue_calls == []


def test_status_command_reports_task_state(monkeypatch):
    agent = _agent(monkeypatch)
    task_id = asyncio.run(agent.handle("build: do a thing", _context()))
    task_id = task_id.split("`")[1]

    result = asyncio.run(agent.handle(f"status {task_id}", _context()))

    assert task_id in result
    assert "pending" in result


def test_status_command_unknown_task(monkeypatch):
    agent = _agent(monkeypatch)

    result = asyncio.run(agent.handle("status bld_missing", _context()))

    assert "No build task found" in result


def test_cancel_command_cancels_pending_task(monkeypatch):
    agent = _agent(monkeypatch)
    ack = asyncio.run(agent.handle("build: do a thing", _context()))
    task_id = ack.split("`")[1]

    result = asyncio.run(agent.handle(f"cancel {task_id}", _context()))

    assert "Cancelled build task" in result
    assert agent.tasks.get(task_id)["status"] == "cancelled"

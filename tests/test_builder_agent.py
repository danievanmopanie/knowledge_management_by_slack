"""Tests for the Slack-facing natural-language Builder Agent."""

import asyncio

from src.agents.builder.agent import BuilderAgent, extract_pr_number
from src.core.config import settings
from src.core.context import RequestContext


class FakeBuilderTaskStore:
    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.enqueue_calls: list[dict] = []

    def enqueue(
        self,
        *,
        goal,
        requester_id,
        channel_id,
        thread_ts,
        handoff_pr_number=None,
    ):
        task_id = f"bld_{len(self.tasks) + 1}"
        call = {
            "goal": goal,
            "requester_id": requester_id,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "handoff_pr_number": handoff_pr_number,
        }
        self.enqueue_calls.append(call)
        self.tasks[task_id] = {
            "task_id": task_id,
            "goal": goal,
            "status": "pending",
            "branch_name": None,
            "pr_url": None,
            "handoff_pr_number": handoff_pr_number,
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

    def request_cancel(self, task_id):
        task = self.tasks.get(task_id)
        if task and task["status"] == "running":
            task["cancel_requested"] = True
            return True
        return False

    def count_ahead(self, task_id):
        order = list(self.tasks.keys())
        idx = order.index(task_id)
        return sum(
            1 for other_id in order[:idx] if self.tasks[other_id]["status"] in ("pending", "running")
        )


def _agent(monkeypatch, allowed_user_ids: str = "U1"):
    monkeypatch.setattr(settings, "builder_agent_allowed_user_ids", allowed_user_ids)
    agent = BuilderAgent.__new__(BuilderAgent)
    agent.tasks = FakeBuilderTaskStore()
    return agent


def _context(user_id: str = "U1") -> RequestContext:
    return RequestContext.from_slack(channel_id="C1", user_id=user_id, thread_ts="123.45")


def test_help_explains_natural_language_and_on_device_execution(monkeypatch):
    agent = _agent(monkeypatch)
    result = asyncio.run(agent.handle("", _context()))
    assert "Talk to me naturally" in result
    assert "No `build:` prefix is required" in result
    assert "copy/paste" in result


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
    assert call["handoff_pr_number"] is None
    assert "bld_1" in result


def test_external_pr_handoff_is_extracted_from_natural_request(monkeypatch):
    agent = _agent(monkeypatch)
    result = asyncio.run(
        agent.handle("Take PR #83 and action it on the GX10. Make sure it works.", _context())
    )

    call = agent.tasks.enqueue_calls[0]
    assert call["handoff_pr_number"] == "83"
    assert "taking PR #83 onto the GX10" in result


def test_external_pr_handoff_accepts_github_url(monkeypatch):
    agent = _agent(monkeypatch)
    asyncio.run(
        agent.handle(
            "Action https://github.com/danievanmopanie/knowledge_management_by_slack/pull/72",
            _context(),
        )
    )
    assert agent.tasks.enqueue_calls[0]["handoff_pr_number"] == "72"


def test_extract_pr_number_does_not_treat_bare_issue_number_as_pr():
    assert extract_pr_number("Please fix #83") is None
    assert extract_pr_number("Please action PR83") == "83"
    assert extract_pr_number("pull request 91 needs testing") == "91"


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


def test_cancel_command_asks_running_task_to_stop(monkeypatch):
    agent = _agent(monkeypatch)
    ack = asyncio.run(agent.handle("Do a thing", _context()))
    task_id = ack.split("`")[1]
    agent.tasks.tasks[task_id]["status"] = "running"

    result = asyncio.run(agent.handle(f"cancel {task_id}", _context()))

    assert "safely between steps" in result
    assert agent.tasks.tasks[task_id].get("cancel_requested") is True
    assert agent.tasks.get(task_id)["status"] == "running"  # worker flips this once it notices


def test_first_turn_in_queue_gets_no_queue_note(monkeypatch):
    agent = _agent(monkeypatch)
    result = asyncio.run(agent.handle("Do a thing", _context()))
    assert "ahead of yours" not in result


def test_queued_turn_reports_position_and_eta(monkeypatch):
    agent = _agent(monkeypatch)
    asyncio.run(agent.handle("first request", _context()))
    result = asyncio.run(agent.handle("second request", _context()))

    assert "1 turn ahead of yours" in result
    assert "bld_2" in result


def test_status_command_reports_queue_position_for_pending_task(monkeypatch):
    agent = _agent(monkeypatch)
    asyncio.run(agent.handle("first request", _context()))
    ack = asyncio.run(agent.handle("second request", _context()))
    task_id = ack.split("`")[1]

    result = asyncio.run(agent.handle(f"status {task_id}", _context()))

    assert "Queue position: 1 turn(s) ahead" in result

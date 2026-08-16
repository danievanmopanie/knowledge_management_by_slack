"""Tests for Builder Agent Block Kit interactivity (Cancel button)."""

import asyncio
from unittest.mock import AsyncMock

from src.bot import builder_interactivity
from src.bot.blockkit import ids
from src.core.config import settings


class FakeApp:
    def __init__(self):
        self.actions = {}

    def action(self, action_id):
        def register(handler):
            self.actions[action_id] = handler
            return handler

        return register


def registered_app():
    app = FakeApp()
    builder_interactivity.register(app)
    return app


def fake_client():
    return AsyncMock()


class FakeTaskStore:
    def __init__(self, status: str, pr_url: str | None = "https://github.com/org/repo/pull/83"):
        self.status = status
        self.pr_url = pr_url
        self.requested_cancel: list[str] = []
        self.cancelled: list[str] = []

    def get(self, task_id):
        return {"task_id": task_id, "status": self.status, "pr_url": self.pr_url}

    def request_cancel(self, task_id):
        self.requested_cancel.append(task_id)
        return True

    def mark_cancelled(self, task_id):
        self.cancelled.append(task_id)
        return True


class FakeDeploymentStore:
    def __init__(self):
        self.records: list[dict] = []
        self.results: list[dict] = []

    def record(self, **kwargs):
        deployment_id = f"dep_{len(self.records) + 1}"
        self.records.append({"deployment_id": deployment_id, **kwargs})
        return deployment_id

    def mark_result(self, deployment_id, **kwargs):
        self.results.append({"deployment_id": deployment_id, **kwargs})


def cancel_body(task_id="bld_123", user_id="U1", channel_id="C1"):
    return {
        "actions": [{"value": task_id}],
        "channel": {"id": channel_id},
        "user": {"id": user_id},
    }


def merge_deploy_body(task_id="bld_123", user_id="U1", channel_id="C1", message_ts="123.45"):
    return {
        "actions": [{"value": task_id}],
        "channel": {"id": channel_id},
        "user": {"id": user_id},
        "message": {"ts": message_ts},
    }


async def _invoke_and_drain(handler, *args):
    """Await the handler, then drain any background task it scheduled via
    asyncio.create_task (Merge & Deploy dispatches its work that way so the
    3-second Slack ack deadline isn't blocked by it)."""
    await handler(*args)
    pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending)


def test_cancel_button_acks_before_doing_any_work(monkeypatch):
    monkeypatch.setattr(settings, "builder_agent_allowed_user_ids", "U1")
    order: list[str] = []
    ack = AsyncMock(side_effect=lambda: order.append("ack"))
    client = fake_client()
    client.chat_postEphemeral.side_effect = lambda **kwargs: order.append("chat_postEphemeral")
    monkeypatch.setattr(
        "src.bot.builder_interactivity.BuilderTaskStore", lambda: FakeTaskStore("running")
    )

    app = registered_app()
    asyncio.run(app.actions[ids.BUILDER_CANCEL_TURN](ack, cancel_body(), client))

    assert order[0] == "ack"


def test_cancel_button_denies_users_outside_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "builder_agent_allowed_user_ids", "UADMIN")
    store = FakeTaskStore("running")
    monkeypatch.setattr("src.bot.builder_interactivity.BuilderTaskStore", lambda: store)
    client = fake_client()

    app = registered_app()
    asyncio.run(app.actions[ids.BUILDER_CANCEL_TURN](AsyncMock(), cancel_body(user_id="UOTHER"), client))

    assert store.requested_cancel == []
    client.chat_postEphemeral.assert_awaited_once()
    assert "not on the Builder Agent allowlist" in client.chat_postEphemeral.call_args.kwargs["text"]


def test_cancel_button_requests_cancel_for_running_task(monkeypatch):
    monkeypatch.setattr(settings, "builder_agent_allowed_user_ids", "U1")
    store = FakeTaskStore("running")
    monkeypatch.setattr("src.bot.builder_interactivity.BuilderTaskStore", lambda: store)
    client = fake_client()

    app = registered_app()
    asyncio.run(app.actions[ids.BUILDER_CANCEL_TURN](AsyncMock(), cancel_body(task_id="bld_123"), client))

    assert store.requested_cancel == ["bld_123"]
    client.chat_postEphemeral.assert_awaited_once()
    assert "safely between steps" in client.chat_postEphemeral.call_args.kwargs["text"]


def test_cancel_button_never_updates_the_persistent_card(monkeypatch):
    """Ownership invariant: only the worker process transitions the card's
    status via chat_update. The button handler only posts ephemeral feedback."""
    monkeypatch.setattr(settings, "builder_agent_allowed_user_ids", "U1")
    store = FakeTaskStore("running")
    monkeypatch.setattr("src.bot.builder_interactivity.BuilderTaskStore", lambda: store)
    client = fake_client()

    app = registered_app()
    asyncio.run(app.actions[ids.BUILDER_CANCEL_TURN](AsyncMock(), cancel_body(), client))

    client.chat_update.assert_not_called()


def test_merge_deploy_button_acks_before_any_work(monkeypatch):
    monkeypatch.setattr(settings, "builder_agent_allowed_user_ids", "U1")
    order: list[str] = []
    ack = AsyncMock(side_effect=lambda: order.append("ack"))
    client = fake_client()
    client.chat_update.side_effect = lambda **kwargs: order.append("chat_update")
    monkeypatch.setattr("src.bot.builder_interactivity.BuilderTaskStore", lambda: FakeTaskStore("succeeded"))
    monkeypatch.setattr("src.bot.builder_interactivity.BuilderDeploymentStore", FakeDeploymentStore)
    monkeypatch.setattr(
        "src.bot.builder_interactivity.merge_and_deploy",
        lambda **kwargs: SimpleDeployResult(success=True, merge_sha="abc", message="Merged; deployed."),
    )

    app = registered_app()
    asyncio.run(_invoke_and_drain(app.actions[ids.BUILDER_MERGE_DEPLOY], ack, merge_deploy_body(), client))

    assert order[0] == "ack"


def test_merge_deploy_button_denies_users_outside_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "builder_agent_allowed_user_ids", "UADMIN")
    client = fake_client()
    monkeypatch.setattr("src.bot.builder_interactivity.BuilderTaskStore", lambda: FakeTaskStore("succeeded"))
    monkeypatch.setattr(
        "src.bot.builder_interactivity.merge_and_deploy",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not merge/deploy for a disallowed user")),
    )

    app = registered_app()
    asyncio.run(
        _invoke_and_drain(
            app.actions[ids.BUILDER_MERGE_DEPLOY], AsyncMock(), merge_deploy_body(user_id="UOTHER"), client
        )
    )

    client.chat_postEphemeral.assert_awaited_once()
    assert "not on the Builder Agent allowlist" in client.chat_postEphemeral.call_args.kwargs["text"]
    client.chat_update.assert_not_called()


def test_merge_deploy_button_requires_a_stored_pr_url(monkeypatch):
    monkeypatch.setattr(settings, "builder_agent_allowed_user_ids", "U1")
    client = fake_client()
    monkeypatch.setattr(
        "src.bot.builder_interactivity.BuilderTaskStore", lambda: FakeTaskStore("succeeded", pr_url=None)
    )
    monkeypatch.setattr(
        "src.bot.builder_interactivity.merge_and_deploy",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not merge/deploy without a PR")),
    )

    app = registered_app()
    asyncio.run(
        _invoke_and_drain(app.actions[ids.BUILDER_MERGE_DEPLOY], AsyncMock(), merge_deploy_body(), client)
    )

    client.chat_postEphemeral.assert_awaited_once()
    assert "No pull request found" in client.chat_postEphemeral.call_args.kwargs["text"]


class SimpleDeployResult:
    def __init__(self, *, success, merge_sha, message, restarted=None):
        self.success = success
        self.merge_sha = merge_sha
        self.message = message
        self.restarted = restarted or []


def test_merge_deploy_button_updates_card_on_success(monkeypatch):
    monkeypatch.setattr(settings, "builder_agent_allowed_user_ids", "U1")
    client = fake_client()
    deployment_store = FakeDeploymentStore()
    monkeypatch.setattr("src.bot.builder_interactivity.BuilderTaskStore", lambda: FakeTaskStore("succeeded"))
    monkeypatch.setattr("src.bot.builder_interactivity.BuilderDeploymentStore", lambda: deployment_store)
    monkeypatch.setattr(
        "src.bot.builder_interactivity.merge_and_deploy",
        lambda **kwargs: SimpleDeployResult(success=True, merge_sha="def456", message="Merged; deployed."),
    )

    app = registered_app()
    asyncio.run(
        _invoke_and_drain(app.actions[ids.BUILDER_MERGE_DEPLOY], AsyncMock(), merge_deploy_body(), client)
    )

    client.chat_update.assert_awaited_once()
    blocks = client.chat_update.call_args.kwargs["blocks"]
    assert "Merged & deployed" in blocks[0]["text"]["text"]
    assert deployment_store.results[0]["success"] is True


def test_merge_deploy_button_updates_card_on_failure(monkeypatch):
    monkeypatch.setattr(settings, "builder_agent_allowed_user_ids", "U1")
    client = fake_client()
    deployment_store = FakeDeploymentStore()
    monkeypatch.setattr("src.bot.builder_interactivity.BuilderTaskStore", lambda: FakeTaskStore("succeeded"))
    monkeypatch.setattr("src.bot.builder_interactivity.BuilderDeploymentStore", lambda: deployment_store)
    monkeypatch.setattr(
        "src.bot.builder_interactivity.merge_and_deploy",
        lambda **kwargs: SimpleDeployResult(success=False, merge_sha=None, message="Merge failed: 409"),
    )

    app = registered_app()
    asyncio.run(
        _invoke_and_drain(app.actions[ids.BUILDER_MERGE_DEPLOY], AsyncMock(), merge_deploy_body(), client)
    )

    client.chat_update.assert_awaited_once()
    blocks = client.chat_update.call_args.kwargs["blocks"]
    assert "Merge & Deploy failed" in blocks[0]["text"]["text"]
    assert deployment_store.results[0]["success"] is False

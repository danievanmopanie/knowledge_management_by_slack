"""Tests for the Builder Agent SQLite task queue."""

from pathlib import Path

from src.agents.builder.task_store import BuilderTaskStore


def test_enqueue_creates_pending_task(tmp_path: Path):
    store = BuilderTaskStore(tmp_path / "platform.db")

    task_id = store.enqueue(
        goal="Add a health check endpoint",
        requester_id="U1",
        channel_id="C1",
        thread_ts="123.45",
    )

    task = store.get(task_id)
    assert task is not None
    assert task["status"] == "pending"
    assert task["goal"] == "Add a health check endpoint"
    assert task["requester_id"] == "U1"
    assert task["channel_id"] == "C1"
    assert task["thread_ts"] == "123.45"
    assert task["attempts"] == 0


def test_claim_next_returns_none_when_queue_empty(tmp_path: Path):
    store = BuilderTaskStore(tmp_path / "platform.db")

    assert store.claim_next() is None


def test_claim_next_claims_oldest_pending_task(tmp_path: Path):
    store = BuilderTaskStore(tmp_path / "platform.db")
    first_id = store.enqueue(goal="first", requester_id="U1", channel_id="C1", thread_ts=None)
    second_id = store.enqueue(goal="second", requester_id="U1", channel_id="C1", thread_ts=None)

    claimed = store.claim_next()

    assert claimed is not None
    assert claimed["task_id"] == first_id
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert claimed["started_at"] is not None

    still_pending = store.get(second_id)
    assert still_pending["status"] == "pending"


def test_claim_next_does_not_double_claim(tmp_path: Path):
    store = BuilderTaskStore(tmp_path / "platform.db")
    store.enqueue(goal="first", requester_id="U1", channel_id="C1", thread_ts=None)
    store.enqueue(goal="second", requester_id="U1", channel_id="C1", thread_ts=None)

    first_claim = store.claim_next()
    second_claim = store.claim_next()
    third_claim = store.claim_next()

    assert first_claim["task_id"] != second_claim["task_id"]
    assert third_claim is None


def test_mark_succeeded_records_pr_url(tmp_path: Path):
    store = BuilderTaskStore(tmp_path / "platform.db")
    task_id = store.enqueue(goal="goal", requester_id="U1", channel_id="C1", thread_ts=None)
    store.claim_next()

    store.mark_succeeded(task_id, pr_url="https://github.com/org/repo/pull/1")

    task = store.get(task_id)
    assert task["status"] == "succeeded"
    assert task["pr_url"] == "https://github.com/org/repo/pull/1"
    assert task["finished_at"] is not None


def test_mark_failed_records_error(tmp_path: Path):
    store = BuilderTaskStore(tmp_path / "platform.db")
    task_id = store.enqueue(goal="goal", requester_id="U1", channel_id="C1", thread_ts=None)
    store.claim_next()

    store.mark_failed(task_id, error_message="aider crashed")

    task = store.get(task_id)
    assert task["status"] == "failed"
    assert task["error_message"] == "aider crashed"


def test_mark_cancelled_only_applies_to_pending(tmp_path: Path):
    store = BuilderTaskStore(tmp_path / "platform.db")
    first_id = store.enqueue(goal="goal", requester_id="U1", channel_id="C1", thread_ts=None)
    second_id = store.enqueue(goal="goal2", requester_id="U1", channel_id="C1", thread_ts=None)
    store.claim_next()  # claims the oldest task (first_id), flips it to running

    assert store.mark_cancelled(first_id) is False
    assert store.get(first_id)["status"] == "running"

    assert store.mark_cancelled(second_id) is True
    assert store.get(second_id)["status"] == "cancelled"


def test_list_by_requester_orders_newest_first(tmp_path: Path):
    store = BuilderTaskStore(tmp_path / "platform.db")
    first_id = store.enqueue(goal="first", requester_id="U1", channel_id="C1", thread_ts=None)
    second_id = store.enqueue(goal="second", requester_id="U1", channel_id="C1", thread_ts=None)

    rows = store.list_by_requester("U1")

    assert [row["task_id"] for row in rows] == [second_id, first_id]

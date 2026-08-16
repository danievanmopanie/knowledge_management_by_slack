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
    assert task["handoff_pr_number"] is None
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
    store.claim_next()

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


def test_followup_turn_inherits_latest_published_pr_in_same_thread(tmp_path: Path):
    store = BuilderTaskStore(tmp_path / "platform.db")
    first_id = store.enqueue(
        goal="first change",
        requester_id="U1",
        channel_id="C1",
        thread_ts="100.1",
    )
    store.mark_running(first_id, branch_name=f"builder/{first_id}")
    store.mark_succeeded(first_id, pr_url="https://github.com/org/repo/pull/9")

    followup_id = store.enqueue(
        goal="also add tests",
        requester_id="U1",
        channel_id="C1",
        thread_ts="100.1",
    )
    followup = store.get(followup_id)

    assert followup is not None
    assert followup["continuation_branch"] == f"builder/{first_id}"
    assert followup["continuation_pr_url"] == "https://github.com/org/repo/pull/9"


def test_explicit_handoff_overrides_previous_thread_pr(tmp_path: Path):
    store = BuilderTaskStore(tmp_path / "platform.db")
    first_id = store.enqueue(
        goal="first change",
        requester_id="U1",
        channel_id="C1",
        thread_ts="100.1",
    )
    store.mark_running(first_id, branch_name=f"builder/{first_id}")
    store.mark_succeeded(first_id, pr_url="https://github.com/org/repo/pull/9")

    handoff_id = store.enqueue(
        goal="take PR #83",
        requester_id="U1",
        channel_id="C1",
        thread_ts="100.1",
        handoff_pr_number="83",
    )
    handoff = store.get(handoff_id)

    assert handoff is not None
    assert handoff["handoff_pr_number"] == "83"
    assert handoff["continuation_branch"] is None
    assert handoff["continuation_pr_url"] is None


def test_new_thread_does_not_inherit_previous_pr(tmp_path: Path):
    store = BuilderTaskStore(tmp_path / "platform.db")
    first_id = store.enqueue(
        goal="first change",
        requester_id="U1",
        channel_id="C1",
        thread_ts="100.1",
    )
    store.mark_running(first_id, branch_name=f"builder/{first_id}")
    store.mark_succeeded(first_id, pr_url="https://github.com/org/repo/pull/9")

    unrelated_id = store.enqueue(
        goal="new work",
        requester_id="U1",
        channel_id="C1",
        thread_ts="200.2",
    )
    unrelated = store.get(unrelated_id)

    assert unrelated is not None
    assert unrelated["continuation_branch"] is None
    assert unrelated["continuation_pr_url"] is None

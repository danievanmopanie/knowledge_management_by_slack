"""Tests for the Builder Merge & Deploy audit trail."""

from pathlib import Path

from src.agents.builder.deployment_store import BuilderDeploymentStore


def test_record_creates_started_deployment(tmp_path: Path):
    store = BuilderDeploymentStore(tmp_path / "platform.db")

    deployment_id = store.record(
        task_id="bld_1",
        pr_number="83",
        pr_url="https://github.com/org/repo/pull/83",
        triggered_by="U1",
        channel_id="C1",
        message_ts="123.45",
    )

    record = store.get(deployment_id)
    assert record is not None
    assert record["status"] == "started"
    assert record["pr_number"] == "83"
    assert record["triggered_by"] == "U1"
    assert record["finished_at"] is None


def test_mark_result_records_success(tmp_path: Path):
    store = BuilderDeploymentStore(tmp_path / "platform.db")
    deployment_id = store.record(
        task_id="bld_1",
        pr_number="83",
        pr_url="https://github.com/org/repo/pull/83",
        triggered_by="U1",
        channel_id="C1",
        message_ts="123.45",
    )

    store.mark_result(
        deployment_id,
        success=True,
        merge_sha="def456",
        restarted=[("svc.service", True, "active")],
        error_message=None,
    )

    record = store.get(deployment_id)
    assert record["status"] == "succeeded"
    assert record["merge_sha"] == "def456"
    assert "svc.service" in record["restarted_units"]
    assert record["finished_at"] is not None


def test_mark_result_records_failure(tmp_path: Path):
    store = BuilderDeploymentStore(tmp_path / "platform.db")
    deployment_id = store.record(
        task_id=None,
        pr_number="83",
        pr_url="https://github.com/org/repo/pull/83",
        triggered_by="U1",
        channel_id="C1",
        message_ts=None,
    )

    store.mark_result(
        deployment_id, success=False, merge_sha=None, restarted=[], error_message="merge failed"
    )

    record = store.get(deployment_id)
    assert record["status"] == "failed"
    assert record["error_message"] == "merge failed"


def test_get_unknown_deployment_returns_none(tmp_path: Path):
    store = BuilderDeploymentStore(tmp_path / "platform.db")
    assert store.get("dep_missing") is None

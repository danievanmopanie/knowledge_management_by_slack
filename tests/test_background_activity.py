from __future__ import annotations

import threading

from src.bot.blockkit.builder import builder_status_blocks
from src.ux.background_activity import BackgroundActivity


def test_builder_progress_card_shows_elapsed_and_heartbeat() -> None:
    blocks = builder_status_blocks(
        task_id="bld_123",
        status="running",
        summary="Running tests",
        branch_name="builder/bld_123",
        elapsed_seconds=95,
        idle_seconds=31,
        heartbeat=True,
    )

    rendered = str(blocks)
    assert "1m 35s" in rendered
    assert "Still working" in rendered
    assert "31s" in rendered
    assert "bld_123" in rendered


def test_background_activity_emits_immediate_update_and_heartbeat() -> None:
    updates = []
    heartbeat_seen = threading.Event()

    def capture(snapshot) -> None:
        updates.append(snapshot)
        if snapshot.heartbeat:
            heartbeat_seen.set()

    activity = BackgroundActivity(capture, heartbeat_seconds=1)
    activity.start("running", "Inspecting repository")
    try:
        assert updates
        assert updates[0].heartbeat is False
        assert updates[0].summary == "Inspecting repository"
        assert heartbeat_seen.wait(2.5)
        assert any(item.heartbeat for item in updates)
    finally:
        activity.stop()


def test_background_activity_phase_change_is_immediate() -> None:
    updates = []
    activity = BackgroundActivity(updates.append, heartbeat_seconds=60)
    activity.start("running", "Inspecting")
    try:
        activity.set_phase("testing", "Running tests")
    finally:
        activity.stop()

    assert updates[-1].phase == "testing"
    assert updates[-1].summary == "Running tests"
    assert updates[-1].heartbeat is False

"""Tests for Builder persistent Block Kit status cards."""

from src.bot.blockkit.builder import builder_status_blocks


def test_builder_status_card_is_compact_and_actionable():
    blocks = builder_status_blocks(
        task_id="bld_123",
        status="completed",
        summary="The change is green and ready for review.",
        branch_name="builder/bld_123",
        validation="✅ passed",
        repair_attempt="1",
        pr_url="https://github.com/example/repo/pull/1",
    )

    assert blocks[0]["type"] == "header"
    assert "Ready for review" in blocks[0]["text"]["text"]
    actions = [block for block in blocks if block["type"] == "actions"]
    assert len(actions) == 1
    button = actions[0]["elements"][0]
    assert button["url"].endswith("/pull/1")
    assert button["text"]["text"] == "Open pull request"


def test_builder_answer_card_has_no_fake_pr_action():
    blocks = builder_status_blocks(
        task_id="bld_123",
        status="answered",
        summary="Repository inspection only; no files changed.",
    )

    assert not any(block["type"] == "actions" for block in blocks)


def test_builder_status_card_shows_current_step_context():
    blocks = builder_status_blocks(
        task_id="bld_123",
        status="running",
        summary="Working on it.",
        current_step="run_shell: pytest -q",
    )

    context_texts = [
        element["text"]
        for block in blocks
        if block["type"] == "context"
        for element in block["elements"]
    ]
    assert any("run_shell: pytest -q" in text for text in context_texts)


def test_builder_status_card_shows_cancel_button_when_requested():
    blocks = builder_status_blocks(
        task_id="bld_123",
        status="running",
        summary="Working on it.",
        show_cancel=True,
    )

    actions = [block for block in blocks if block["type"] == "actions"]
    assert len(actions) == 1
    assert actions[0]["elements"][0]["action_id"] == "builder_cancel_turn"
    assert actions[0]["elements"][0]["value"] == "bld_123"


def test_builder_status_card_hides_cancel_button_by_default():
    blocks = builder_status_blocks(
        task_id="bld_123",
        status="running",
        summary="Working on it.",
    )

    assert not any(block["type"] == "actions" for block in blocks)


def test_builder_status_card_shows_merge_deploy_button_with_confirm():
    blocks = builder_status_blocks(
        task_id="bld_123",
        status="completed",
        summary="Ready.",
        pr_url="https://github.com/example/repo/pull/1",
        show_merge_deploy=True,
    )

    actions = [block for block in blocks if block["type"] == "actions"]
    assert len(actions) == 1
    merge_button = next(
        element for element in actions[0]["elements"] if element["action_id"] == "builder_merge_deploy"
    )
    assert merge_button["value"] == "bld_123"
    assert "confirm" in merge_button


def test_builder_status_card_hides_merge_deploy_button_by_default():
    blocks = builder_status_blocks(
        task_id="bld_123",
        status="completed",
        summary="Ready.",
        pr_url="https://github.com/example/repo/pull/1",
    )

    actions = [block for block in blocks if block["type"] == "actions"]
    action_ids = {element["action_id"] for element in actions[0]["elements"] if "action_id" in element}
    assert "builder_merge_deploy" not in action_ids


def test_builder_status_card_new_status_copy():
    cancelled = builder_status_blocks(task_id="bld_1", status="cancelled", summary="Stopped.")
    timed_out = builder_status_blocks(task_id="bld_2", status="timed_out", summary="Stopped.")

    assert "Cancelled" in cancelled[0]["text"]["text"]
    assert "deadline" in timed_out[0]["text"]["text"]

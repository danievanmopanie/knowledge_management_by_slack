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

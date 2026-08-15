from src.bot.blockkit.decisions import decision_actions
from src.bot.frontend_actions import ADD_INCIDENT_NUMBER, build_incident_number_blocks
from src.bot.frontend_modals import (
    MODAL_CLARIFICATION_OTHER,
    MODAL_INCIDENT_NUMBER,
    clarification_other_modal,
    incident_number_modal,
)


def test_shared_decision_actions_are_button_first():
    block = decision_actions(
        block_id="test_decision",
        value="abc123",
        primary_action_id="approve",
        primary_label="Approve",
        secondary_action_id="cancel",
        secondary_label="Cancel",
        secondary_style="danger",
    )

    assert block["type"] == "actions"
    assert block["elements"][0]["type"] == "button"
    assert block["elements"][0]["action_id"] == "approve"
    assert block["elements"][0]["style"] == "primary"
    assert block["elements"][1]["action_id"] == "cancel"
    assert block["elements"][1]["style"] == "danger"


def test_frontend_incident_prompt_uses_button_not_typed_command():
    blocks = build_incident_number_blocks("C123", "171234.567")
    action_blocks = [block for block in blocks if block.get("type") == "actions"]

    assert len(action_blocks) == 1
    button = action_blocks[0]["elements"][0]
    assert button["action_id"] == ADD_INCIDENT_NUMBER
    assert button["text"]["text"] == "Add incident number"


def test_frontend_structured_inputs_use_modals():
    incident = incident_number_modal(channel_id="C123", thread_ts="171234.567")
    other = clarification_other_modal(
        channel_id="C123",
        thread_ts="171234.567",
        key="application",
        source_message_ts="171235.000",
    )

    assert incident["type"] == "modal"
    assert incident["callback_id"] == MODAL_INCIDENT_NUMBER
    assert incident["blocks"][0]["element"]["type"] == "plain_text_input"

    assert other["type"] == "modal"
    assert other["callback_id"] == MODAL_CLARIFICATION_OTHER
    assert other["blocks"][0]["element"]["type"] == "plain_text_input"

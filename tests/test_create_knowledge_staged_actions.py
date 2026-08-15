from src.bot.blockkit import ids
from src.bot.create_knowledge_staged import staged_incident_blocks, staged_upload_blocks


def _actions(blocks):
    return [
        element
        for block in blocks
        if block.get("type") == "actions"
        for element in block.get("elements") or []
    ]


def test_incident_stage_has_build_and_cancel_buttons():
    blocks = staged_incident_blocks(
        "*Upload staged — not yet searchable*\n"
        "*ServiceNow Incident export detected:* *incident (Jul).csv* → `stg_047db93a2c61`"
    )
    actions = _actions(blocks)

    assert [action["action_id"] for action in actions] == [
        ids.CREATE_KNOWLEDGE_CONFIRM_STAGE,
        ids.CREATE_KNOWLEDGE_CANCEL_STAGE,
    ]
    assert actions[0]["text"]["text"] == "Build knowledge"
    assert actions[0]["style"] == "primary"
    assert actions[0]["value"] == "stg_047db93a2c61"
    assert actions[1]["text"]["text"] == "Cancel"
    assert actions[1]["style"] == "danger"


def test_generic_stage_uses_same_decision_pattern():
    blocks = staged_upload_blocks(
        "*Upload staged — not yet searchable*\n"
        "• *runbook.md* → `stg_abc123` (1,200 extracted characters)"
    )
    actions = _actions(blocks)

    assert len(actions) == 2
    assert actions[0]["action_id"] == ids.CREATE_KNOWLEDGE_CONFIRM_STAGE
    assert actions[1]["action_id"] == ids.CREATE_KNOWLEDGE_CANCEL_STAGE

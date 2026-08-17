import json

from src.agents.frontend_support.clarification import (
    CLARIFICATION_ACTION,
    ClarificationEngine,
    ClarificationStore,
)


def test_clarification_buttons_use_unique_action_ids_and_preserve_payload(tmp_path):
    engine = ClarificationEngine(ClarificationStore(tmp_path / "platform.db"))
    question = engine.next_question("C1", "T1", "App keeps timing out")
    blocks = engine.blocks(question, "C1", "T1")
    actions = next(block for block in blocks if block.get("type") == "actions")

    action_ids = [element["action_id"] for element in actions["elements"]]
    assert len(action_ids) == len(set(action_ids))
    assert all(action_id.startswith(f"{CLARIFICATION_ACTION}_") for action_id in action_ids)

    for element in actions["elements"]:
        payload = json.loads(element["value"])
        assert payload["channel_id"] == "C1"
        assert payload["thread_ts"] == "T1"
        assert payload["key"] == "application"
        assert payload["value"]

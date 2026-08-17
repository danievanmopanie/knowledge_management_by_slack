from src.agents.frontend_support.clarification import (
    MAX_CLARIFICATION_ROUNDS,
    ClarificationEngine,
    ClarificationStore,
)


def _engine(tmp_path):
    return ClarificationEngine(ClarificationStore(tmp_path / "platform.db"))


def test_generic_app_timeout_asks_for_application_first(tmp_path):
    engine = _engine(tmp_path)
    question = engine.next_question("C1", "T1", "App keeps timing out")

    assert question is not None
    assert question.key == "application"
    assert "Which application" in question.question
    assert {option.value for option in question.options} >= {"Microsoft Teams", "Outlook", "SAP", "Other"}


def test_specific_app_does_not_ask_application_again(tmp_path):
    engine = _engine(tmp_path)
    question = engine.next_question("C1", "T1", "Microsoft Teams keeps timing out")

    assert question is not None
    assert question.key == "failure_stage"


def test_specific_timeout_stage_skips_unnecessary_clarification(tmp_path):
    engine = _engine(tmp_path)
    question = engine.next_question(
        "C1", "T1", "Microsoft Teams times out during calls and meetings"
    )

    assert question is None


def test_unambiguous_problem_does_not_get_questionnaire(tmp_path):
    engine = _engine(tmp_path)
    question = engine.next_question(
        "C1", "T1", "Outlook repeatedly prompts for credentials after password reset"
    )

    assert question is None


def test_free_text_answer_satisfies_pending_question(tmp_path):
    engine = _engine(tmp_path)
    first = engine.next_question("C1", "T1", "App keeps timing out")
    assert first is not None
    engine.store.ask("C1", "T1", first.key)

    consumed = engine.store.consume_free_text("C1", "T1", "Citrix Workspace")

    assert consumed == ("application", "Citrix Workspace")
    state = engine.store.state("C1", "T1")
    assert state["pending_key"] is None
    assert state["answers"]["application"] == "Citrix Workspace"


def test_clarification_is_capped_at_three_rounds(tmp_path):
    engine = _engine(tmp_path)

    for round_number in range(1, MAX_CLARIFICATION_ROUNDS + 1):
        actual = engine.store.ask("C1", "T1", f"key_{round_number}")
        assert actual == round_number
        engine.store.answer("C1", "T1", f"key_{round_number}", "answer")

    assert engine.store.state("C1", "T1")["round_number"] == 3
    assert engine.next_question("C1", "T1", "App keeps timing out") is None


def test_blocks_keep_free_text_escape_hatch_and_unique_action_ids(tmp_path):
    engine = _engine(tmp_path)
    question = engine.next_question("C1", "T1", "App keeps timing out")
    blocks = engine.blocks(question, "C1", "T1")

    assert any("reply in the thread" in block.get("elements", [{}])[0].get("text", "") for block in blocks if block.get("type") == "context")
    actions = next(block for block in blocks if block.get("type") == "actions")
    assert 2 <= len(actions["elements"]) <= 5
    action_ids = [element["action_id"] for element in actions["elements"]]
    assert len(action_ids) == len(set(action_ids))

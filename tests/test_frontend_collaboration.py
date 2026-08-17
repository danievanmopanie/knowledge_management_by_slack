"""Tests for collaborative #frontend-support thread intelligence."""

from src.agents.frontend_support.collaboration import (
    FrontendCollaborationService,
    FrontendThreadStore,
    MessageKind,
)
from src.knowledge.graphstore import GraphStore
from src.knowledge.support_graph import SupportKnowledgeGraph


def _service(tmp_path):
    return FrontendCollaborationService(
        store=FrontendThreadStore(tmp_path / "platform.db"),
        graph=SupportKnowledgeGraph(GraphStore(path=tmp_path / "graph")),
    )


def test_social_message_does_not_invoke_support_agent(tmp_path):
    service = _service(tmp_path)

    decision = service.observe(
        channel_id="C-FRONT",
        message_ts="100.1",
        thread_ts=None,
        user_id="U1",
        text="Nice one Jacob, great work!",
    )

    assert decision.kind == MessageKind.SOCIAL
    assert decision.invoke_agent is False
    assert decision.prompt_for_incident is False


def test_new_support_signal_prompts_requester_for_incident_number(tmp_path):
    service = _service(tmp_path)

    decision = service.observe(
        channel_id="C-FRONT",
        message_ts="100.1",
        thread_ts=None,
        user_id="U-REQUESTER",
        text="Outlook keeps prompting the user for a password and is not working correctly.",
    )

    assert decision.kind == MessageKind.SUPPORT_SIGNAL
    assert decision.invoke_agent is True
    assert decision.prompt_for_incident is True
    state = service.store.get_thread("C-FRONT", "100.1")
    assert state.requester_id == "U-REQUESTER"
    assert state.incident_number is None


def test_only_requester_supplied_incident_number_binds_to_thread(tmp_path):
    service = _service(tmp_path)
    service.observe(
        channel_id="C-FRONT",
        message_ts="100.1",
        thread_ts=None,
        user_id="U-REQUESTER",
        text="Teams crashes every time the customer joins a meeting.",
    )

    service.observe(
        channel_id="C-FRONT",
        message_ts="100.2",
        thread_ts="100.1",
        user_id="U-HELPER",
        text="Might be the same as INC0099999.",
    )
    assert service.store.get_thread("C-FRONT", "100.1").incident_number is None

    service.observe(
        channel_id="C-FRONT",
        message_ts="100.3",
        thread_ts="100.1",
        user_id="U-REQUESTER",
        text="The actual incident is INC0012345.",
    )
    assert service.store.get_thread("C-FRONT", "100.1").incident_number == "INC0012345"


def test_resolution_detects_resolver_and_allows_requester_or_resolver_to_confirm(tmp_path):
    service = _service(tmp_path)
    service.observe(
        channel_id="C-FRONT",
        message_ts="100.1",
        thread_ts=None,
        user_id="U-REQUESTER",
        text="VPN authentication fails for the customer.",
    )
    service.observe(
        channel_id="C-FRONT",
        message_ts="100.2",
        thread_ts="100.1",
        user_id="U-REQUESTER",
        text="INC0012345",
    )
    service.observe(
        channel_id="C-FRONT",
        message_ts="100.3",
        thread_ts="100.1",
        user_id="U-TECH",
        text="I reset the cached authentication token and tested the VPN again.",
    )

    decision = service.observe(
        channel_id="C-FRONT",
        message_ts="100.4",
        thread_ts="100.1",
        user_id="U-TECH",
        text="That's fixed it, working now.",
    )

    assert decision.kind == MessageKind.POSSIBLE_RESOLUTION
    assert decision.prompt_for_capture is True
    state = service.store.get_thread("C-FRONT", "100.1")
    assert state.resolver_id == "U-TECH"

    denied = service.confirm_knowledge("C-FRONT", "100.1", "U-RANDOM")
    assert denied.startswith("Only the original requester")

    captured = service.confirm_knowledge("C-FRONT", "100.1", "U-TECH")
    assert captured == "Captured this resolution as trusted reusable knowledge for INC0012345."
    assert service.store.get_thread("C-FRONT", "100.1").status == "resolved"


def test_resolution_requires_incident_before_capture(tmp_path):
    service = _service(tmp_path)
    service.observe(
        channel_id="C-FRONT",
        message_ts="100.1",
        thread_ts=None,
        user_id="U-REQUESTER",
        text="Printer queue keeps failing.",
    )
    service.observe(
        channel_id="C-FRONT",
        message_ts="100.2",
        thread_ts="100.1",
        user_id="U-TECH",
        text="That's fixed it.",
    )

    result = service.confirm_knowledge("C-FRONT", "100.1", "U-TECH")
    assert result.startswith("Please add the ServiceNow incident number")


def test_thread_query_preserves_attribution_and_steps(tmp_path):
    service = _service(tmp_path)
    service.observe(
        channel_id="C-FRONT",
        message_ts="100.1",
        thread_ts=None,
        user_id="U1",
        text="Outlook keeps prompting for credentials.",
    )
    service.observe(
        channel_id="C-FRONT",
        message_ts="100.2",
        thread_ts="100.1",
        user_id="U2",
        text="I tried recreating the Outlook profile but it failed.",
    )

    query = service.build_agent_query("C-FRONT", "100.1")

    assert "Requester: U1" in query
    assert "U1 [support_signal]" in query
    assert "U2 [troubleshooting]" in query
    assert "recreating the Outlook profile" in query


def test_we_have_got_this_suppresses_agent_but_keeps_capturing_events(tmp_path):
    service = _service(tmp_path)
    service.observe(
        channel_id="C-FRONT",
        message_ts="200.1",
        thread_ts=None,
        user_id="U1",
        text="VPN is failing after password reset.",
    )
    muted = service.observe(
        channel_id="C-FRONT",
        message_ts="200.2",
        thread_ts="200.1",
        user_id="U2",
        text="We've got this, we'll take it from here.",
    )
    assert muted.kind == MessageKind.ASSISTANT_SUPPRESS
    assert muted.assistant_suppressed is True

    contribution = service.observe(
        channel_id="C-FRONT",
        message_ts="200.3",
        thread_ts="200.1",
        user_id="U2",
        text="I reset the certificate and tested the VPN again.",
    )
    assert contribution.kind == MessageKind.TROUBLESHOOTING
    assert contribution.invoke_agent is False
    events = service.store.recent_events("C-FRONT", "200.1")
    assert any("reset the certificate" in event["text"] for event in events)


def test_assistant_can_be_called_back_into_muted_thread(tmp_path):
    service = _service(tmp_path)
    service.observe(
        channel_id="C-FRONT",
        message_ts="210.1",
        thread_ts=None,
        user_id="U1",
        text="Printer service is failing.",
    )
    service.observe(
        channel_id="C-FRONT",
        message_ts="210.2",
        thread_ts="210.1",
        user_id="U1",
        text="Assistant, stay quiet.",
    )
    resumed = service.observe(
        channel_id="C-FRONT",
        message_ts="210.3",
        thread_ts="210.1",
        user_id="U1",
        text="Assistant, help again please.",
    )
    assert resumed.kind == MessageKind.ASSISTANT_RESUME
    assert service.store.get_thread("C-FRONT", "210.1").assistant_suppressed is False

    next_turn = service.observe(
        channel_id="C-FRONT",
        message_ts="210.4",
        thread_ts="210.1",
        user_id="U1",
        text="The printer service is still failing after restart.",
    )
    assert next_turn.invoke_agent is True


def test_private_coaching_memory_never_enters_public_thread_events(tmp_path):
    service = _service(tmp_path)
    private_query = service.observe_private(
        channel_id="D-PRIVATE",
        message_ts="300.1",
        user_id="U1",
        text="I don't understand WAM. Can you explain the Outlook suggestion?",
    )
    assert "PRIVATE COACHING SESSION" in private_query
    assert "I don't understand WAM" in private_query

    private_events = service.store.recent_private_events("D-PRIVATE", "U1")
    assert len(private_events) == 1

    service.observe(
        channel_id="C-FRONT",
        message_ts="301.1",
        thread_ts=None,
        user_id="U1",
        text="Outlook keeps prompting for credentials.",
    )
    public_query = service.build_agent_query("C-FRONT", "301.1")
    assert "I don't understand WAM" not in public_query


def test_knowledge_gap_task_is_deduplicated_per_support_thread(tmp_path):
    service = _service(tmp_path)
    service.observe(
        channel_id="C-FRONT",
        message_ts="400.1",
        thread_ts=None,
        user_id="U-REQUESTER",
        text="Outlook repeatedly prompts for credentials.",
    )
    service.observe(
        channel_id="C-FRONT",
        message_ts="400.2",
        thread_ts="400.1",
        user_id="U-REQUESTER",
        text="INC0048219",
    )
    service.observe(
        channel_id="C-FRONT",
        message_ts="400.3",
        thread_ts="400.1",
        user_id="U-JACOB",
        text="I cleared the WAM token cache and tested Outlook.",
    )
    service.observe(
        channel_id="C-FRONT",
        message_ts="400.4",
        thread_ts="400.1",
        user_id="U-REQUESTER",
        text="Working now.",
    )

    first = service.create_knowledge_gap_task(
        "C-FRONT", "400.1", "U-REQUESTER", "No strong formal knowledge matched."
    )
    second = service.create_knowledge_gap_task(
        "C-FRONT", "400.1", "U-REQUESTER", "No strong formal knowledge matched."
    )

    assert first["task_id"] == second["task_id"]
    assert first["incident_number"] == "INC0048219"
    assert "cleared the WAM token cache" in first["resolution"]
    assert "Working now" in first["resolution"]
    assert set(first["contributors"]) == {"U-REQUESTER", "U-JACOB"}


def test_classify_detects_knowledge_edit_requests():
    assert (
        FrontendCollaborationService.classify("That KB article is outdated")
        == MessageKind.KNOWLEDGE_EDIT_REQUEST
    )
    assert (
        FrontendCollaborationService.classify(
            "Update the knowledge article to mention the new driver"
        )
        == MessageKind.KNOWLEDGE_EDIT_REQUEST
    )
    assert (
        FrontendCollaborationService.classify("Flag that article for review")
        == MessageKind.KNOWLEDGE_EDIT_REQUEST
    )
    assert (
        FrontendCollaborationService.classify("The article needs updating")
        == MessageKind.KNOWLEDGE_EDIT_REQUEST
    )


def test_observe_flags_a_knowledge_edit_request(tmp_path):
    service = _service(tmp_path)
    service.observe(
        channel_id="C-FRONT",
        message_ts="500.1",
        thread_ts=None,
        user_id="U-REQUESTER",
        text="Outlook keeps failing to sync mail on the laptop.",
    )

    decision = service.observe(
        channel_id="C-FRONT",
        message_ts="500.2",
        thread_ts="500.1",
        user_id="U-REQUESTER",
        text="That KB article is outdated, it should also mention the profile rebuild step.",
    )

    assert decision.kind == MessageKind.KNOWLEDGE_EDIT_REQUEST
    assert decision.prompt_for_knowledge_edit is True
    assert "profile rebuild" in decision.edit_note


def test_suppressed_thread_does_not_prompt_for_knowledge_edit(tmp_path):
    service = _service(tmp_path)
    service.observe(
        channel_id="C-FRONT",
        message_ts="500.1",
        thread_ts=None,
        user_id="U-REQUESTER",
        text="Outlook keeps failing to sync mail on the laptop.",
    )
    service.store.set_suppressed("C-FRONT", "500.1", True)

    decision = service.observe(
        channel_id="C-FRONT",
        message_ts="500.2",
        thread_ts="500.1",
        user_id="U-REQUESTER",
        text="That KB article is outdated.",
    )

    assert decision.prompt_for_knowledge_edit is False

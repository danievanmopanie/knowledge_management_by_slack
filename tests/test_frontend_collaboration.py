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

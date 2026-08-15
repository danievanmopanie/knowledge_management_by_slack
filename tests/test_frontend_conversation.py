"""Regression tests for natural Frontend Support conversation handling."""

from src.agents.frontend_support.collaboration import (
    FrontendCollaborationService,
    FrontendThreadStore,
)
from src.agents.frontend_support.conversation import (
    clean_mention_text,
    compose_thread_query,
    looks_like_support,
)
from src.knowledge.graphstore import GraphStore
from src.knowledge.support_graph import SupportKnowledgeGraph


def _service(tmp_path):
    return FrontendCollaborationService(
        store=FrontendThreadStore(tmp_path / "platform.db"),
        graph=SupportKnowledgeGraph(GraphStore(path=tmp_path / "graph")),
    )


def test_bluetooth_headset_is_recognised_as_natural_support_language():
    assert looks_like_support("User says their bluetooth headset isn't connecting") is True


def test_mentions_are_removed_without_losing_request():
    assert clean_mention_text("<@U012ABC> - help with this.") == "help with this."


def test_help_with_this_reconstructs_root_problem(tmp_path):
    service = _service(tmp_path)
    service.observe(
        channel_id="C-FRONT",
        message_ts="100.1",
        thread_ts=None,
        user_id="U-REQUESTER",
        text="User says their bluetooth headset isn't connecting",
    )
    service.observe(
        channel_id="C-FRONT",
        message_ts="100.2",
        thread_ts="100.1",
        user_id="U-REQUESTER",
        text="help with this",
    )

    query = compose_thread_query(
        service,
        channel_id="C-FRONT",
        thread_ts="100.1",
        latest_text="help with this",
    )

    assert "bluetooth headset isn't connecting" in query.lower()
    assert "help with this" in query.lower()


def test_thread_query_keeps_other_technician_contribution(tmp_path):
    service = _service(tmp_path)
    service.observe(
        channel_id="C-FRONT",
        message_ts="200.1",
        thread_ts=None,
        user_id="U1",
        text="Bluetooth headset isn't connecting.",
    )
    service.observe(
        channel_id="C-FRONT",
        message_ts="200.2",
        thread_ts="200.1",
        user_id="U2",
        text="I removed the headset from Bluetooth devices and paired it again, but it still fails.",
    )

    query = compose_thread_query(
        service,
        channel_id="C-FRONT",
        thread_ts="200.1",
        latest_text="what next?",
    )

    assert "U2" in query
    assert "paired it again" in query
    assert "what next?" in query

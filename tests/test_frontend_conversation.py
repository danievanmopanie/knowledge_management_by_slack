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
from src.bot.frontend_support_app import _normalise_message_event
from src.knowledge.graphstore import GraphStore
from src.knowledge.support_graph import SupportKnowledgeGraph


def _service(tmp_path):
    return FrontendCollaborationService(
        store=FrontendThreadStore(tmp_path / "platform.db"),
        graph=SupportKnowledgeGraph(GraphStore(path=tmp_path / "graph")),
    )


def test_bluetooth_headset_is_recognised_as_natural_support_language():
    assert looks_like_support("User says their bluetooth headset isn't connecting") is True


def test_bsod_is_recognised_as_high_signal_support_language():
    assert looks_like_support("User's screen shows BSOD") is True
    assert looks_like_support("Laptop is stuck on a blue screen") is True


def test_timeout_phrase_reaches_clarification_layer_without_mention():
    assert looks_like_support("App keeps timing out") is True
    assert looks_like_support("Application times out after a few minutes") is True


def test_mentions_are_removed_without_losing_request():
    assert clean_mention_text("<@U012ABC> - help with this.") == "help with this."


def test_edited_human_message_is_normalised_and_reprocessed():
    event = {
        "subtype": "message_changed",
        "channel": "C-FRONT",
        "channel_type": "channel",
        "message": {
            "type": "message",
            "user": "U1",
            "text": "User's screen shows BSOD",
            "ts": "300.1",
        },
    }
    effective = _normalise_message_event(event)
    assert effective is not None
    assert effective["channel"] == "C-FRONT"
    assert effective["text"] == "User's screen shows BSOD"
    assert looks_like_support(effective["text"]) is True


def test_edited_bot_message_is_ignored():
    event = {
        "subtype": "message_changed",
        "channel": "C-FRONT",
        "message": {
            "type": "message",
            "subtype": "bot_message",
            "text": "updated bot response",
            "ts": "300.2",
        },
    }
    assert _normalise_message_event(event) is None


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

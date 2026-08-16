from datetime import datetime, timedelta, timezone

from src.knowledge.graphstore import GraphStore
from src.knowledge.support_graph import SupportKnowledgeGraph, age_days


def test_support_graph_captures_symptom_action_resolution_and_people(tmp_path):
    graph = SupportKnowledgeGraph(GraphStore(path=tmp_path))

    incident = graph.add_incident(
        "INC0012345",
        short_description="Outlook prompts for credentials",
        state="Resolved",
        assigned_to="Jane Tech",
        caller="John User",
    )
    graph.add_symptom(incident, "Repeated credential prompts", confidence=0.95)
    graph.add_action(
        incident,
        "Rebuild Outlook profile",
        outcome="failed",
        contributor="Jacob Tech",
    )
    graph.add_resolution(
        incident,
        "Clear WAM token cache",
        resolver="Jane Tech",
        root_cause="Corrupted authentication token",
        confidence=0.90,
    )
    graph.save()

    related = graph.related(incident, depth=2)
    relations = {item["relation"] for item in related}
    entity_types = {item["type"] for item in related}

    assert "has_symptom" in relations
    assert "tried" in relations
    assert "failed_action" in relations
    assert "successful_fix" in relations
    assert "person" in entity_types
    assert "symptom" in entity_types
    assert "action" in entity_types
    assert "resolution" in entity_types


def test_related_orders_facts_by_recency_and_annotates_age(tmp_path):
    graph = SupportKnowledgeGraph(GraphStore(path=tmp_path))
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=400)).isoformat()
    recent = (now - timedelta(days=2)).isoformat()

    old_incident = graph.add_incident("INC0000001", short_description="Printer offline", occurred_at=old)
    graph.add_resolution(old_incident, "Reseated the USB cable", confidence=1.0, occurred_at=old)

    new_incident = graph.add_incident("INC0000002", short_description="Printer offline", occurred_at=recent)
    graph.add_resolution(new_incident, "Reinstalled the print driver", confidence=1.0, occurred_at=recent)

    old_related = graph.related(old_incident, depth=1, order_by_recency=True)
    new_related = graph.related(new_incident, depth=1, order_by_recency=True)

    old_fix = next(item for item in old_related if item["relation"] == "successful_fix")
    new_fix = next(item for item in new_related if item["relation"] == "successful_fix")

    assert old_fix["age_days"] >= 400
    assert new_fix["age_days"] <= 3
    assert old_fix["age_days"] > new_fix["age_days"]


def test_related_since_filter_drops_facts_before_cutoff(tmp_path):
    graph = SupportKnowledgeGraph(GraphStore(path=tmp_path))
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=400)).isoformat()
    recent = (now - timedelta(days=2)).isoformat()
    cutoff = (now - timedelta(days=30)).isoformat()

    incident = graph.add_incident("INC0000003", occurred_at=recent)
    graph.add_action(incident, "Rebooted the device", outcome="tried", occurred_at=old)
    graph.add_action(incident, "Cleared the print spooler", outcome="tried", occurred_at=recent)

    related = graph.related(incident, depth=1, since=cutoff)
    tried_names = {item["properties"].get("name") for item in related if item["relation"] == "tried"}

    assert "Cleared the print spooler" in tried_names
    assert "Rebooted the device" not in tried_names


def test_age_days_handles_missing_and_invalid_timestamps():
    assert age_days(None) is None
    assert age_days("not-a-timestamp") is None
    assert age_days(datetime.now(timezone.utc).isoformat()) == 0

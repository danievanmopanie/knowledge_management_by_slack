from src.knowledge.graphstore import GraphStore
from src.knowledge.support_graph import SupportKnowledgeGraph


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

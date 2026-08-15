from src.knowledge.graphstore import GraphStore
from src.knowledge.support_graph import SupportKnowledgeGraph


def test_closed_incident_assignee_is_assignment_fact_not_inferred_resolver(tmp_path):
    graph = SupportKnowledgeGraph(GraphStore(path=tmp_path / "support_graph"))

    incident = graph.add_incident(
        "INC0092846",
        state="Closed",
        assigned_to="Technician One",
    )
    related = graph.related(incident)

    relations = {(item["relation"], item["properties"].get("name")) for item in related}
    assert ("assigned_to", "Technician One") in relations
    assert ("resolved_by", "Technician One") not in relations


def test_resolved_by_is_created_only_from_explicit_enriched_resolver(tmp_path):
    graph = SupportKnowledgeGraph(GraphStore(path=tmp_path / "support_graph"))
    incident = graph.add_incident("INC0092846", state="Closed", assigned_to="Technician One")

    resolution = graph.add_resolution(
        incident,
        "Removed stale cached Office credentials",
        resolution_pattern="Clear cached Office credentials",
        resolver="Technician Two",
        confidence=0.94,
    )

    incident_relations = graph.related(incident)
    assert any(
        item["relation"] == "successful_fix" and item["properties"].get("name") == "Clear cached Office credentials"
        for item in incident_relations
    )

    resolution_relations = graph.related(resolution)
    assert any(
        item["relation"] == "resolved_by" and item["properties"].get("name") == "Technician Two"
        for item in resolution_relations
    )

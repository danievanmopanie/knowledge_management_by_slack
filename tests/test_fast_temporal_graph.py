from datetime import datetime, timezone

from src.knowledge.fast_temporal_graph import FastTemporalGraphBuilder
from src.knowledge.graphstore import GraphStore
from src.reporting.incidents import Incident


def _inc(group: str, updated_hour: int):
    return Incident(
        number="INC1",
        short_description="Application timeout after CHG123456",
        state="In Progress",
        assignment_group=group,
        caller="Alice",
        opened_at=datetime(2026, 8, 15, 7, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 15, updated_hour, 0, tzinfo=timezone.utc),
    )


def test_temporal_graph_closes_old_assignment_and_keeps_history(tmp_path):
    store = GraphStore(tmp_path / "graph")
    builder = FastTemporalGraphBuilder(store)

    builder.build(
        [_inc("Service Desk", 8)],
        observed_at="2026-08-15T08:05:00+00:00",
        source_file="a.csv",
        save=False,
    )
    builder.build(
        [_inc("Desktop", 9)],
        observed_at="2026-08-15T09:05:00+00:00",
        source_file="b.csv",
        save=False,
    )

    edges = [
        (target, data)
        for _, target, _, data in store.graph.out_edges("incident:INC1", keys=True, data=True)
        if data.get("relation") == "assigned_to_group"
    ]
    assert len(edges) == 2
    active = [(target, data) for target, data in edges if data.get("valid_to") is None]
    closed = [(target, data) for target, data in edges if data.get("valid_to") is not None]
    assert len(active) == 1
    assert active[0][0] == "assignment_group:desktop"
    assert len(closed) == 1
    assert closed[0][0] == "assignment_group:service-desk"

    change_edges = [
        data
        for _, target, _, data in store.graph.out_edges("incident:INC1", keys=True, data=True)
        if target == "change:chg123456"
    ]
    assert change_edges
    assert change_edges[0]["relation"] == "temporally_correlated_with"
    assert change_edges[0]["confidence"] == 0.55

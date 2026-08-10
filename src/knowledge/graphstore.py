"""Lightweight knowledge graph for entity relationships (Graph RAG component)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx

from src.core.config import settings


class GraphStore:
    """
    Simple persistent knowledge graph using NetworkX.

    Nodes = entities (systems, error codes, floors, teams, services, etc.)
    Edges = relationships (related_to, causes, owned_by, located_on, ...)
    """

    def __init__(self, path: Path | None = None):
        self.path = (path or settings.vectorstore_path) / "graph.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.graph = nx.MultiDiGraph()
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.graph = nx.node_link_graph(data, directed=True, multigraph=True)

    def save(self) -> None:
        data = nx.node_link_data(self.graph)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add_entity(self, entity_id: str, entity_type: str, **properties: Any) -> None:
        self.graph.add_node(entity_id, type=entity_type, **properties)

    def add_relation(
        self,
        source: str,
        target: str,
        relation: str,
        **properties: Any,
    ) -> None:
        self.graph.add_edge(source, target, key=relation, relation=relation, **properties)

    def get_related(
        self,
        entity_id: str,
        max_depth: int = 1,
        relation_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return outgoing relationships up to max_depth, preserving parallel edge types."""
        if entity_id not in self.graph:
            return []

        results: list[dict[str, Any]] = []
        visited = {entity_id}
        current_layer = {entity_id}

        for depth in range(1, max_depth + 1):
            next_layer: set[str] = set()
            for node in current_layer:
                for _, neighbor, _key, edge_data in self.graph.out_edges(
                    node, data=True, keys=True
                ):
                    rel = edge_data.get("relation")
                    if relation_filter and rel != relation_filter:
                        continue

                    # A MultiDiGraph may contain multiple meaningful relations
                    # between the same two entities (for example TRIED and
                    # FAILED_ACTION). Always return the edge; only traversal is
                    # de-duplicated by node.
                    results.append(
                        {
                            "entity": neighbor,
                            "type": self.graph.nodes[neighbor].get("type"),
                            "relation": rel,
                            "depth": depth,
                            "properties": dict(self.graph.nodes[neighbor]),
                            "edge_properties": dict(edge_data),
                        }
                    )
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_layer.add(neighbor)
            current_layer = next_layer
            if not current_layer:
                break

        return results

    def search_entities(self, query: str, limit: int = 10) -> list[str]:
        """Simple case-insensitive substring search over entity IDs and names."""
        q = query.lower()
        matches = []
        for node, data in self.graph.nodes(data=True):
            name = str(data.get("name", node)).lower()
            if q in node.lower() or q in name:
                matches.append(node)
            if len(matches) >= limit:
                break
        return matches

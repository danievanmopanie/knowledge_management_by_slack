"""Lightweight temporal knowledge graph for entity relationships (Graph RAG component)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx

from src.core.config import settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GraphStore:
    """
    Simple persistent, temporally-aware knowledge graph using NetworkX.

    Nodes = entities (systems, error codes, floors, teams, services, etc.)
    Edges = relationships (related_to, causes, owned_by, located_on, ...)

    Every node tracks first_seen_at/last_seen_at and every edge tracks
    occurred_at (when the underlying fact happened, backdatable for historical
    import) and created_at (when the edge was recorded), so callers can reason
    about recency and staleness instead of treating the graph as timeless.
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

    def add_entity(
        self,
        entity_id: str,
        entity_type: str,
        *,
        occurred_at: str | None = None,
        **properties: Any,
    ) -> None:
        timestamp = occurred_at or _now_iso()
        if entity_id in self.graph:
            existing = self.graph.nodes[entity_id]
            first_seen_at = existing.get("first_seen_at") or timestamp
            last_seen_at = max(existing.get("last_seen_at") or timestamp, timestamp)
        else:
            first_seen_at = timestamp
            last_seen_at = timestamp
        self.graph.add_node(
            entity_id,
            type=entity_type,
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
            **properties,
        )

    def add_relation(
        self,
        source: str,
        target: str,
        relation: str,
        *,
        occurred_at: str | None = None,
        **properties: Any,
    ) -> None:
        timestamp = occurred_at or _now_iso()
        self.graph.add_edge(
            source,
            target,
            key=relation,
            relation=relation,
            occurred_at=timestamp,
            created_at=_now_iso(),
            **properties,
        )

    def get_related(
        self,
        entity_id: str,
        max_depth: int = 1,
        relation_filter: str | None = None,
        *,
        since: str | None = None,
        order_by_recency: bool = False,
    ) -> list[dict[str, Any]]:
        """Return outgoing relationships up to max_depth, preserving parallel edge types.

        `since` (an ISO timestamp) drops facts that occurred earlier than it.
        `order_by_recency` sorts the most recently occurred facts first, which
        is what makes repeat-incident and "is this fix still current" reasoning
        possible instead of an unordered bag of relationships.
        """
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
                    occurred_at = edge_data.get("occurred_at")
                    if since and occurred_at and occurred_at < since:
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
                            "occurred_at": occurred_at,
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

        if order_by_recency:
            results.sort(key=lambda item: item.get("occurred_at") or "", reverse=True)

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

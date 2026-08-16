#!/usr/bin/env python3
"""Rebuild trusted support vectors and pattern rollups without re-running the LLM."""

from __future__ import annotations

from src.core.config import settings
from src.knowledge.knowledge_trust import rebuild_trusted_index, rebuild_trusted_patterns
from src.knowledge.organisational_knowledge import (
    OrganisationalKnowledgeIndex,
    OrganisationalKnowledgeStore,
)
from src.knowledge.support_extraction import extraction_model_key


def main() -> int:
    store = OrganisationalKnowledgeStore()
    index = OrganisationalKnowledgeIndex()
    model_key = extraction_model_key()

    with store._connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM support_incident_knowledge").fetchone()[0])
        trusted = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM support_incident_knowledge
                WHERE model=? AND confidence>=?
                """,
                (model_key, settings.knowledge_min_extraction_confidence),
            ).fetchone()[0]
        )

    print("Trusted support knowledge rebuild")
    print(f"Stored enriched incidents: {total:,}")
    print(f"Trusted current-schema incidents: {trusted:,}")
    print(f"Model/schema: {model_key}")

    index_result = rebuild_trusted_index(store, index, model_key=model_key)
    patterns = rebuild_trusted_patterns(store, model_key=model_key)

    print(f"Trusted vectors: {index_result['collection_total']:,}")
    print(f"Documents written: {index_result['documents']:,}")
    print(f"Materialised trusted patterns: {patterns:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

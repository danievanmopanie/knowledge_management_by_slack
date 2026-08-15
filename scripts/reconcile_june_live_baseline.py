#!/usr/bin/env python3
"""Reconcile the live incident stores to the archived June snapshot.

This removes only incident benchmark residue while preserving the expensive June
embeddings. The command is dry-run by default. Use --apply only after reviewing
the plan.

What --apply does:
1. prune the `incidents` Chroma collection to the exact June focused-vector IDs;
2. prune incident semantic hashes to June incident numbers;
3. reset only incident temporal tables in platform.db;
4. remove only `incident:*` nodes (and their edges) from the shared NetworkX graph;
5. replay June as the clean temporal baseline.

Because the June vectors/hashes already exist, the replay should normally create
zero new embeddings and simply refresh metadata while rebuilding the cheap graph
and temporal spine.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import settings
from src.knowledge.fast_incident_ingest import (
    FIELD_GROUPS,
    FastTemporalIncidentIngestor,
    _fast_field_document,
)
from src.knowledge.graphstore import GraphStore
from src.knowledge.incident_dedupe import load_hash_index, save_hash_index
from src.knowledge.vectorstore import VectorStore
from src.reporting.incidents import load_incidents_from_csv

DEFAULT_JUNE = settings.incidents_path / "incident (Jun).csv"
HASH_PATH = settings.vectorstore_path.parent / "incident_content_hashes.json"
TEMPORAL_TABLES = (
    "incident_current",
    "incident_versions",
    "incident_transitions",
    "assignment_dwell",
    "incident_ingest_runs",
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _active_jobs(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "incident_ingest_jobs"):
        return 0
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM incident_ingest_jobs WHERE status IN ('pending','running')"
        ).fetchone()[0]
    )


def _expected_june(source: Path):
    incidents = load_incidents_from_csv(source)
    unique = {inc.number: inc for inc in incidents if inc.number}
    vector_ids: set[str] = set()
    for inc in unique.values():
        for group in FIELD_GROUPS:
            if _fast_field_document(inc, group):
                vector_ids.add(f"incident-{inc.number}-{group}")
    return unique, vector_ids


def _numbers_from_vector_ids(ids: set[str]) -> set[str]:
    numbers: set[str] = set()
    suffixes = tuple(f"-{group}" for group in FIELD_GROUPS)
    for value in ids:
        if not value.startswith("incident-"):
            continue
        body = value[len("incident-") :]
        for suffix in suffixes:
            if body.endswith(suffix):
                numbers.add(body[: -len(suffix)])
                break
    return numbers


def _backup_small_state(graph: GraphStore) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = settings.backup_root / f"june-baseline-reconcile-{stamp}"
    root.mkdir(parents=True, exist_ok=True)

    db_backup = root / "platform.db"
    with sqlite3.connect(settings.platform_db_path) as source_conn:
        with sqlite3.connect(db_backup) as backup_conn:
            source_conn.backup(backup_conn)

    if graph.path.exists():
        shutil.copy2(graph.path, root / "graph.json")
    if HASH_PATH.exists():
        shutil.copy2(HASH_PATH, root / "incident_content_hashes.json")
    return root


def _delete_in_batches(vector: VectorStore, ids: set[str], size: int = 5000) -> None:
    ordered = sorted(ids)
    for start in range(0, len(ordered), size):
        vector.delete_documents(ordered[start : start + size])


def _print_plan(
    *,
    source: Path,
    june_count: int,
    expected_vectors: int,
    current_vectors: int,
    extra_vectors: set[str],
    missing_vectors: set[str],
    hash_count: int,
    extra_hashes: set[str],
    temporal_counts: dict[str, int],
    graph_incidents: int,
    active_jobs: int,
) -> None:
    print("=== JUNE LIVE BASELINE RECONCILIATION — DRY RUN ===")
    print(f"June source:             {source}")
    print(f"June incidents:          {june_count:,}")
    print(f"Expected June vectors:   {expected_vectors:,}")
    print(f"Current incident vectors:{current_vectors:>10,}")
    print(f"Extra vectors to remove: {len(extra_vectors):,}")
    print(f"Missing June vectors:    {len(missing_vectors):,}")
    print(f"Current semantic hashes: {hash_count:,}")
    print(f"Extra hashes to remove:  {len(extra_hashes):,}")
    print(f"Current graph incidents: {graph_incidents:,}")
    print(f"Active ingest jobs:      {active_jobs:,}")
    print("Temporal rows:")
    for table, count in temporal_counts.items():
        print(f"  {table:<24} {count:,}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely reconcile the permanent incident stores to June as the clean baseline."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_JUNE,
        help=f"Archived June CSV (default: {DEFAULT_JUNE})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the reconciliation. Without this flag the command is read-only.",
    )
    args = parser.parse_args()

    source = args.csv.expanduser().resolve()
    if not source.exists():
        print(f"ERROR: June source not found: {source}", file=sys.stderr)
        return 2

    unique, expected_ids = _expected_june(source)
    june_numbers = set(unique)
    if not june_numbers:
        print("ERROR: no recognisable June incidents", file=sys.stderr)
        return 2

    vector = VectorStore(collection_name="incidents", embedding_purpose="incident")
    current_ids = set(vector.list_ids())
    extra_vectors = current_ids - expected_ids
    missing_vectors = expected_ids - current_ids

    hashes = load_hash_index(HASH_PATH)
    extra_hashes = set(hashes) - june_numbers

    graph = GraphStore()
    graph_incident_nodes = {
        node
        for node, data in graph.graph.nodes(data=True)
        if str(node).startswith("incident:") or data.get("type") == "incident"
    }

    settings.platform_db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.platform_db_path) as conn:
        temporal_counts = {table: _table_count(conn, table) for table in TEMPORAL_TABLES}
        active_jobs = _active_jobs(conn)

    _print_plan(
        source=source,
        june_count=len(june_numbers),
        expected_vectors=len(expected_ids),
        current_vectors=len(current_ids),
        extra_vectors=extra_vectors,
        missing_vectors=missing_vectors,
        hash_count=len(hashes),
        extra_hashes=extra_hashes,
        temporal_counts=temporal_counts,
        graph_incidents=len(graph_incident_nodes),
        active_jobs=active_jobs,
    )

    if not args.apply:
        print("\nNo changes made. Review the counts, then run again with --apply.")
        return 0

    if active_jobs:
        print(
            "ERROR: pending/running incident ingest jobs exist. Let them finish or stop the worker before reconciling.",
            file=sys.stderr,
        )
        return 2

    backup = _backup_small_state(graph)
    print(f"\nBackup created: {backup}")

    # Remove benchmark-only vectors while preserving every June vector already embedded.
    _delete_in_batches(vector, extra_vectors)

    # If any expected June vector is unexpectedly missing, drop that incident's hash so
    # the replay repairs it by embedding that incident again rather than skipping it.
    repair_numbers = _numbers_from_vector_ids(missing_vectors)
    clean_hashes = {
        number: digest
        for number, digest in hashes.items()
        if number in june_numbers and number not in repair_numbers
    }
    save_hash_index(clean_hashes, HASH_PATH)

    # Reset only incident temporal state. Other platform tables, including the durable
    # ingest-job queue, remain untouched.
    with sqlite3.connect(settings.platform_db_path, timeout=30) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        for table in TEMPORAL_TABLES:
            if _table_exists(conn, table):
                conn.execute(f"DELETE FROM {table}")
        conn.commit()

    # Remove incident nodes and their edges only. Shared/non-incident graph knowledge
    # is preserved; disconnected historical entity nodes are harmless and reusable.
    graph.graph.remove_nodes_from(graph_incident_nodes)
    graph.save(compact=True)

    print("Replaying June as the clean temporal baseline...")
    result = FastTemporalIncidentIngestor().ingest_csv(
        source,
        complete_snapshot=False,
    )
    metrics = result.as_dict()
    print(json.dumps(metrics, indent=2, sort_keys=True))

    final_vector_count = VectorStore(
        collection_name="incidents", embedding_purpose="incident"
    ).count()
    with sqlite3.connect(settings.platform_db_path) as conn:
        final_current = _table_count(conn, "incident_current")
    final_graph = GraphStore()
    final_graph_incidents = sum(
        1
        for node, data in final_graph.graph.nodes(data=True)
        if str(node).startswith("incident:") or data.get("type") == "incident"
    )

    print("\n=== RECONCILIATION VERIFICATION ===")
    print(f"Expected June incidents: {len(june_numbers):,}")
    print(f"Temporal current:        {final_current:,}")
    print(f"Graph incident nodes:    {final_graph_incidents:,}")
    print(f"Expected June vectors:   {len(expected_ids):,}")
    print(f"Incident vectors:        {final_vector_count:,}")
    print(f"Replay vector documents: {result.vector_documents:,}")
    print(f"Metadata-only incidents: {result.vector_metadata_only_incidents:,}")

    healthy = (
        final_current == len(june_numbers)
        and final_graph_incidents == len(june_numbers)
        and final_vector_count == len(expected_ids)
    )
    if not healthy:
        print("ERROR: verification counts do not match June baseline; do not ingest July yet.", file=sys.stderr)
        return 1

    print("\nJune is now the clean permanent incident baseline. July can be ingested next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

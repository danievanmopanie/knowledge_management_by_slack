"""High-throughput ServiceNow incident ingestion for Create Knowledge.

The hot path is deterministic and incremental:
1. parse one CSV snapshot;
2. compare against SQLite current state;
3. persist only changed versions/transitions/dwell;
4. in parallel, embed only new/changed incidents and update the temporal graph.

No LLM is used during ingestion.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import settings
from src.knowledge.fast_temporal_graph import FastTemporalGraphBuilder, GraphBuildResult
from src.knowledge.incident_dedupe import content_hash, load_hash_index, save_hash_index
from src.knowledge.incident_rag import FIELD_GROUPS, _field_document, _incident_metadata
from src.knowledge.temporal_incidents import TemporalIncidentStore, TemporalIngestResult
from src.knowledge.vectorstore import VectorStore
from src.reporting.incidents import Incident, load_incidents_from_csv

INCIDENT_COLLECTION = "incidents"


@dataclass
class IncidentCSVProfile:
    file_name: str
    rows: int
    unique_incidents: int
    problem_docs: int
    troubleshooting_docs: int
    resolution_docs: int
    estimated_vector_documents: int
    description_coverage: float
    work_notes_coverage: float
    comments_coverage: float
    resolution_coverage: float
    parse_seconds: float


@dataclass
class FastEmbedResult:
    incidents: int
    vector_documents: int
    collection_total: int
    elapsed_seconds: float
    documents_per_second: float


@dataclass
class FastIncidentIngestResult:
    run_id: str
    file_name: str
    rows: int
    unique_incidents: int
    new: int
    changed: int
    unchanged: int
    missing: int
    versions_written: int
    transitions_written: int
    dwell_rows_written: int
    vector_documents: int
    vector_collection_total: int
    graph_nodes_added: int
    graph_edges_added: int
    graph_temporal_edges_opened: int
    graph_reference_edges: int
    parse_seconds: float
    temporal_seconds: float
    embedding_seconds: float
    graph_seconds: float
    total_seconds: float
    embedding_documents_per_second: float

    def as_dict(self) -> dict:
        return asdict(self)


def _ratio(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def profile_incident_csv(path: Path) -> IncidentCSVProfile:
    started = time.perf_counter()
    incidents = load_incidents_from_csv(path)
    unique = {inc.number: inc for inc in incidents if inc.number}
    values = list(unique.values())
    group_counts = {group: 0 for group in FIELD_GROUPS}
    for inc in values:
        for group in FIELD_GROUPS:
            if _field_document(inc, group):
                group_counts[group] += 1
    total = len(values)
    return IncidentCSVProfile(
        file_name=path.name,
        rows=len(incidents),
        unique_incidents=total,
        problem_docs=group_counts["problem"],
        troubleshooting_docs=group_counts["troubleshooting"],
        resolution_docs=group_counts["resolution"],
        estimated_vector_documents=sum(group_counts.values()),
        description_coverage=_ratio(sum(bool((i.description or "").strip()) for i in values), total),
        work_notes_coverage=_ratio(sum(bool((i.work_notes or "").strip()) for i in values), total),
        comments_coverage=_ratio(sum(bool((i.comments or "").strip()) for i in values), total),
        resolution_coverage=_ratio(sum(bool((i.resolution_notes or "").strip()) for i in values), total),
        parse_seconds=time.perf_counter() - started,
    )


class FastIncidentEmbedder:
    """Index focused incident vectors without running the legacy graph side effect."""

    def __init__(self, vector_store: VectorStore | None = None):
        self.vector_store = vector_store or VectorStore(
            collection_name=INCIDENT_COLLECTION,
            embedding_purpose="incident",
        )

    def index(self, incidents: list[Incident], *, incident_chunk_size: int = 1000) -> FastEmbedResult:
        started = time.perf_counter()
        if not incidents:
            return FastEmbedResult(0, 0, self.vector_store.count(), 0.0, 0.0)

        hashes = load_hash_index()
        vector_documents = 0
        chunk_size = max(1, int(incident_chunk_size))

        for start in range(0, len(incidents), chunk_size):
            batch = incidents[start : start + chunk_size]
            stale_ids: list[str] = []
            docs: list[str] = []
            metas: list[dict] = []
            ids: list[str] = []

            for inc in batch:
                stale_ids.append(f"incident-{inc.number}")
                stale_ids.extend(f"incident-{inc.number}-{group}" for group in FIELD_GROUPS)
                for group in FIELD_GROUPS:
                    text = _field_document(inc, group)
                    if not text:
                        continue
                    docs.append(text)
                    metas.append(_incident_metadata(inc, group))
                    ids.append(f"incident-{inc.number}-{group}")
                hashes[inc.number] = content_hash(inc)

            self.vector_store.delete_documents(stale_ids)
            if docs:
                self.vector_store.add_documents(
                    docs,
                    metadatas=metas,
                    ids=ids,
                    batch_size=settings.incident_embedding_batch_size,
                )
                vector_documents += len(docs)

        save_hash_index(hashes)
        elapsed = time.perf_counter() - started
        return FastEmbedResult(
            incidents=len(incidents),
            vector_documents=vector_documents,
            collection_total=self.vector_store.count(),
            elapsed_seconds=elapsed,
            documents_per_second=(vector_documents / elapsed) if elapsed > 0 else 0.0,
        )


class FastTemporalIncidentIngestor:
    """One-call benchmarkable ingest pipeline for a full ServiceNow snapshot."""

    def __init__(
        self,
        temporal_store: TemporalIncidentStore | None = None,
        embedder: FastIncidentEmbedder | None = None,
        graph_builder: FastTemporalGraphBuilder | None = None,
    ):
        self.temporal_store = temporal_store or TemporalIncidentStore()
        self.embedder = embedder or FastIncidentEmbedder()
        self.graph_builder = graph_builder or FastTemporalGraphBuilder()

    def ingest_csv(
        self,
        path: Path,
        *,
        complete_snapshot: bool = True,
        observed_at: datetime | None = None,
    ) -> FastIncidentIngestResult:
        total_started = time.perf_counter()
        parse_started = time.perf_counter()
        incidents = load_incidents_from_csv(path)
        parse_seconds = time.perf_counter() - parse_started
        if not incidents:
            raise ValueError(f"{path} does not contain recognisable ServiceNow incidents")

        observed = observed_at or datetime.now(timezone.utc)
        temporal: TemporalIngestResult = self.temporal_store.ingest_snapshot(
            incidents,
            source_file=path.name,
            observed_at=observed,
            complete_snapshot=complete_snapshot,
        )
        changed = temporal.changed_incidents

        observed_text = observed.isoformat()
        # Graph building is CPU/memory work while embeddings are mostly local
        # model/Chroma I/O, so run them concurrently to reduce wall-clock time.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="incident-ingest") as pool:
            graph_future = pool.submit(
                self.graph_builder.build,
                changed,
                observed_at=observed_text,
                source_file=path.name,
                save=True,
            )
            embed_future = pool.submit(self.embedder.index, changed)
            graph: GraphBuildResult = graph_future.result()
            embedded: FastEmbedResult = embed_future.result()

        return FastIncidentIngestResult(
            run_id=temporal.run_id,
            file_name=path.name,
            rows=temporal.rows,
            unique_incidents=temporal.unique_incidents,
            new=temporal.new,
            changed=temporal.changed,
            unchanged=temporal.unchanged,
            missing=temporal.missing,
            versions_written=temporal.versions_written,
            transitions_written=temporal.transitions_written,
            dwell_rows_written=temporal.dwell_rows_written,
            vector_documents=embedded.vector_documents,
            vector_collection_total=embedded.collection_total,
            graph_nodes_added=graph.nodes_added,
            graph_edges_added=graph.edges_added,
            graph_temporal_edges_opened=graph.temporal_edges_opened,
            graph_reference_edges=graph.reference_edges,
            parse_seconds=parse_seconds,
            temporal_seconds=temporal.elapsed_seconds,
            embedding_seconds=embedded.elapsed_seconds,
            graph_seconds=graph.elapsed_seconds,
            total_seconds=time.perf_counter() - total_started,
            embedding_documents_per_second=embedded.documents_per_second,
        )

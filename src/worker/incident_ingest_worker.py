"""Background worker for Create Knowledge ServiceNow incident imports."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from slack_sdk import WebClient

from src.core.config import settings
from src.knowledge.fast_incident_ingest import FastTemporalIncidentIngestor
from src.knowledge.incident_ingest_jobs import IncidentIngestJobStore

logger = logging.getLogger(__name__)
POLL_SECONDS = 3


def _client() -> WebClient | None:
    token = os.getenv("CREATE_KNOWLEDGE_SLACK_BOT_TOKEN", "").strip() or settings.slack_bot_token
    return WebClient(token=token) if token else None


def _post(client: WebClient | None, job: dict, text: str) -> None:
    if not client or not job.get("channel_id"):
        return
    try:
        client.chat_postMessage(
            channel=job["channel_id"],
            thread_ts=job.get("thread_ts"),
            text=text,
        )
    except Exception:
        logger.exception("Could not post incident ingest worker update for %s", job.get("job_id"))


def _result_text(job_id: str, metrics: dict) -> str:
    return (
        f"*Incident import {job_id} complete*\n"
        f"• rows: {metrics['rows']:,}; unique incidents: {metrics['unique_incidents']:,}\n"
        f"• new: {metrics['new']:,}; changed: {metrics['changed']:,}; unchanged: {metrics['unchanged']:,}\n"
        f"• temporal versions: {metrics['versions_written']:,}; transitions: {metrics['transitions_written']:,}; dwell rows: {metrics['dwell_rows_written']:,}\n"
        f"• vector documents: {metrics['vector_documents']:,} at {metrics['embedding_documents_per_second']:.1f} docs/sec\n"
        f"• graph: +{metrics['graph_nodes_added']:,} nodes, +{metrics['graph_edges_added']:,} edges\n"
        f"• timing: parse {metrics['parse_seconds']:.2f}s; temporal {metrics['temporal_seconds']:.2f}s; "
        f"embed {metrics['embedding_seconds']:.2f}s; graph {metrics['graph_seconds']:.2f}s; total {metrics['total_seconds']:.2f}s"
    )


def run_once(store: IncidentIngestJobStore | None = None) -> bool:
    store = store or IncidentIngestJobStore()
    job = store.claim_next()
    if not job:
        return False
    client = _client()
    job_id = job["job_id"]
    _post(
        client,
        job,
        f"*Incident import {job_id} started* — building temporal deltas, graph relationships and focused vectors. No LLM extraction is used in this fast path.",
    )
    try:
        result = FastTemporalIncidentIngestor().ingest_csv(Path(job["file_path"]))
        metrics = result.as_dict()
        store.mark_succeeded(job_id, metrics)
        _post(client, job, _result_text(job_id, metrics))
    except Exception as exc:
        logger.exception("Incident import failed job_id=%s", job_id)
        store.mark_failed(job_id, str(exc))
        _post(client, job, f"Incident import `{job_id}` failed: {exc}")
    return True


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting Create Knowledge incident ingest worker")
    store = IncidentIngestJobStore()
    while True:
        if not run_once(store):
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())

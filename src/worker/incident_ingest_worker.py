"""Background worker for Create Knowledge ServiceNow incident imports."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from slack_sdk import WebClient

from src.bot.create_knowledge_progress import build_blocks
from src.core.config import settings
from src.knowledge.fast_incident_ingest import FastTemporalIncidentIngestor
from src.knowledge.incident_ingest_jobs import IncidentIngestJobStore

logger = logging.getLogger(__name__)
POLL_SECONDS = 3
MIN_UPDATE_SECONDS = 1.0
EMBED_UPDATE_PERCENT = 5


def _client() -> WebClient | None:
    token = os.getenv("CREATE_KNOWLEDGE_SLACK_BOT_TOKEN", "").strip() or settings.slack_bot_token
    return WebClient(token=token) if token else None


def _fallback_text(job: dict, progress: dict) -> str:
    stage = str(progress.get("stage") or "queued").replace("_", " ")
    return f"Create Knowledge `{job.get('job_id')}` — {stage}"


class SlackProgressUpdater:
    """Persist progress and update one Slack build card in place."""

    def __init__(
        self,
        client: WebClient | None,
        store: IncidentIngestJobStore,
        job: dict,
    ):
        self.client = client
        self.store = store
        self.job = dict(job)
        self.state = dict(job.get("progress") or {"stage": "queued"})
        self._lock = threading.Lock()
        self._last_sent = 0.0
        self._last_embedding_pct = -1
        self._last_stage = str(self.state.get("stage") or "queued")

    def _post_or_update(self) -> None:
        if not self.client or not self.job.get("channel_id"):
            return
        blocks = build_blocks(self.job, self.state)
        text = _fallback_text(self.job, self.state)
        message_ts = str(self.job.get("progress_message_ts") or "")
        try:
            if message_ts:
                self.client.chat_update(
                    channel=self.job["channel_id"],
                    ts=message_ts,
                    text=text,
                    blocks=blocks,
                )
            else:
                posted = self.client.chat_postMessage(
                    channel=self.job["channel_id"],
                    thread_ts=self.job.get("thread_ts"),
                    text=text,
                    blocks=blocks,
                )
                message_ts = str(posted.get("ts") or "")
                if message_ts:
                    self.job["progress_message_ts"] = message_ts
                    self.store.set_progress_message(self.job["job_id"], message_ts)
        except Exception:
            logger.exception("Could not update Create Knowledge progress card for %s", self.job.get("job_id"))

    def _should_send(self, payload: dict, force: bool) -> bool:
        if force:
            return True
        now = time.monotonic()
        stage = str(payload.get("stage") or self.state.get("stage") or "")
        stage_changed = stage != self._last_stage
        if stage == "embedding_progress":
            total = int(payload.get("documents_total") or 0)
            done = int(payload.get("documents_done") or 0)
            pct = int((done / total) * 100) if total else 100
            if pct >= self._last_embedding_pct + EMBED_UPDATE_PERCENT:
                self._last_embedding_pct = pct
                return True
        if stage_changed and now - self._last_sent >= MIN_UPDATE_SECONDS:
            return True
        return now - self._last_sent >= 10.0

    def update(self, payload: dict, *, force: bool = False) -> None:
        with self._lock:
            self.state.update(payload)
            self.store.update_progress(self.job["job_id"], self.state)
            if not self._should_send(payload, force):
                return
            self._post_or_update()
            self._last_sent = time.monotonic()
            self._last_stage = str(self.state.get("stage") or self._last_stage)

    def complete(self, metrics: dict) -> None:
        with self._lock:
            self.state = {"stage": "completed", "metrics": metrics}
            self._post_or_update()
            self._last_sent = time.monotonic()
            self._last_stage = "completed"

    def fail(self, error: str) -> None:
        with self._lock:
            self.state = {"stage": "failed", "error": error}
            self._post_or_update()
            self._last_sent = time.monotonic()
            self._last_stage = "failed"


def run_once(store: IncidentIngestJobStore | None = None) -> bool:
    store = store or IncidentIngestJobStore()
    job = store.claim_next()
    if not job:
        return False

    client = _client()
    job_id = job["job_id"]
    updater = SlackProgressUpdater(client, store, job)
    updater.update({"stage": "parse_started"}, force=True)

    try:
        result = FastTemporalIncidentIngestor().ingest_csv(
            Path(job["file_path"]),
            complete_snapshot=False,
            on_progress=updater.update,
        )
        metrics = result.as_dict()
        store.mark_succeeded(job_id, metrics)
        updater.complete(metrics)
    except Exception as exc:
        logger.exception("Incident import failed job_id=%s", job_id)
        store.mark_failed(job_id, str(exc))
        updater.fail(str(exc))
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

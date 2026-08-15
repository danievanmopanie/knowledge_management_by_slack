"""Knowledge Ingest Agent – staged, confirmed uploads from knowledge channels."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.agents.base import BaseAgent
from src.core.audit import AuditStore
from src.core.config import settings
from src.core.context import RequestContext
from src.knowledge.fast_incident_ingest import profile_incident_csv
from src.knowledge.file_loader import UploadValidationError, download_slack_file, extract_text
from src.knowledge.governed_ingest import commit_knowledge
from src.knowledge.incident_ingest_jobs import IncidentIngestJobStore
from src.knowledge.staging import StagingStore
from src.reporting.incidents import load_incidents_from_csv

logger = logging.getLogger(__name__)


def _looks_like_incident_csv(path: Path) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    try:
        return bool(load_incidents_from_csv(path))
    except Exception:
        return False


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


class KnowledgeIngestAgent(BaseAgent):
    """Stages uploads and commits/queues them only after explicit confirmation."""

    name = "knowledge_ingest"

    def __init__(self):
        self.staging = StagingStore()
        self.audit = AuditStore()
        self.incident_jobs = IncidentIngestJobStore()

    async def handle(self, message: str, context: RequestContext) -> str:
        text = (message or "").strip()
        lower = text.lower()

        if lower.startswith("confirm "):
            return self._confirm(text.split(maxsplit=1)[1].strip(), context)
        if lower.startswith("cancel "):
            return self._cancel(text.split(maxsplit=1)[1].strip(), context)
        if lower.startswith("status "):
            return self._status(text.split(maxsplit=1)[1].strip())

        if not context.files:
            return (
                "Upload a supported file to stage it. Nothing becomes searchable until you confirm it.\n"
                "For ServiceNow Incident CSVs I will profile the snapshot first, then queue a fast temporal/vector ingest.\n"
                "Use `confirm <stage-id>`, `cancel <stage-id>`, or `status <ING-job-id>`."
            )

        lines = ["*Upload staged — not yet searchable*"]
        for file_info in context.files:
            name = file_info.get("name") or file_info.get("id") or "unknown"
            try:
                local_path = await download_slack_file(file_info)
                is_incident = _looks_like_incident_csv(local_path)
                extracted_chars = 0
                profile = None
                if is_incident:
                    profile = profile_incident_csv(local_path)
                else:
                    extracted = extract_text(local_path)
                    if not extracted.strip():
                        raise UploadValidationError("No extractable text found")
                    extracted_chars = len(extracted)

                stage_id = self.staging.create(
                    slack_file_id=str(file_info.get("id") or local_path.name),
                    file_name=str(name),
                    local_path=str(local_path),
                    uploader_id=context.user_id,
                    channel_id=context.channel_id,
                )
                metadata = {"file_name": str(name), "incident_export": is_incident}
                if profile:
                    metadata.update(
                        {
                            "rows": profile.rows,
                            "unique_incidents": profile.unique_incidents,
                            "estimated_vector_documents": profile.estimated_vector_documents,
                        }
                    )
                self.audit.record(
                    context,
                    action="knowledge.stage",
                    outcome="success",
                    target_type="staged_upload",
                    target_id=stage_id,
                    metadata=metadata,
                )

                if profile:
                    lines.append(
                        f"\n*ServiceNow Incident export detected:* *{name}* → `{stage_id}`\n"
                        f"• rows: {profile.rows:,}; unique incidents: {profile.unique_incidents:,}\n"
                        f"• estimated focused vector documents: {profile.estimated_vector_documents:,} "
                        f"(problem {profile.problem_docs:,}, troubleshooting {profile.troubleshooting_docs:,}, resolution {profile.resolution_docs:,})\n"
                        f"• text coverage: description {_pct(profile.description_coverage)}, work notes {_pct(profile.work_notes_coverage)}, "
                        f"comments {_pct(profile.comments_coverage)}, resolution {_pct(profile.resolution_coverage)}\n"
                        f"• profile parse time: {profile.parse_seconds:.2f}s\n"
                        f"Confirm with `confirm {stage_id}`. The incident file will use the fast temporal pipeline only — it will not be duplicated as a generic knowledge document."
                    )
                else:
                    lines.append(
                        f"• *{name}* → `{stage_id}` ({extracted_chars:,} extracted characters)\n"
                        f"  Confirm with `confirm {stage_id}` or cancel with `cancel {stage_id}`."
                    )
            except Exception:
                logger.exception("Upload staging failed request_id=%s file=%s", context.request_id, name)
                self.audit.record(
                    context,
                    action="knowledge.stage",
                    outcome="failed",
                    target_type="upload",
                    target_id=str(file_info.get("id") or name),
                )
                lines.append(f"• *{name}*: staging failed (reference `{context.request_id}`)")
        return "\n".join(lines)

    def _confirm(self, stage_id: str, context: RequestContext) -> str:
        staged = self.staging.get(stage_id)
        if not staged or staged["status"] != "staged":
            return f"I cannot confirm `{stage_id}` because it is missing or no longer staged."
        if staged.get("uploader_id") and context.user_id != staged.get("uploader_id"):
            self.audit.record(
                context,
                action="knowledge.confirm",
                outcome="denied",
                target_type="staged_upload",
                target_id=stage_id,
            )
            return "Only the person who staged this upload may confirm it."

        path = Path(staged["local_path"])
        try:
            if _looks_like_incident_csv(path):
                settings.incidents_path.mkdir(parents=True, exist_ok=True)
                dest = settings.incidents_path / path.name
                if path.resolve() != dest.resolve():
                    shutil.copy2(path, dest)
                job_id = self.incident_jobs.enqueue(
                    file_path=str(dest),
                    file_name=staged["file_name"],
                    requester_id=context.user_id,
                    channel_id=context.channel_id,
                    thread_ts=context.thread_ts,
                )
                self.staging.set_status(stage_id, "confirmed")
                self.audit.record(
                    context,
                    action="incident_ingest.enqueue",
                    outcome="success",
                    target_type="incident_ingest_job",
                    target_id=job_id,
                    metadata={"stage_id": stage_id, "file_name": staged["file_name"]},
                )
                return (
                    f"Confirmed `{stage_id}` and queued fast incident import `{job_id}`. "
                    "The worker will compare the snapshot, embed only new/changed incidents, and build the deterministic temporal graph in parallel. "
                    f"Use `status {job_id}` at any time."
                )

            text = extract_text(path)
            result = commit_knowledge(
                text=text,
                title=staged["file_name"],
                source_id=staged["slack_file_id"],
                owner_id=context.user_id,
            )
            self.staging.set_status(stage_id, "confirmed")
            self.audit.record(
                context,
                action="knowledge.confirm",
                outcome="success",
                target_type="document",
                target_id=result["document_id"],
                metadata={"unchanged": bool(result["unchanged"]), "chunks": result["chunks"]},
            )
            if result["unchanged"]:
                return f"Confirmed `{stage_id}`. Content is unchanged, so no duplicate chunks were created."
            return f"Confirmed `{stage_id}` → {result['chunks']} searchable chunk(s)."
        except Exception:
            self.staging.set_status(stage_id, "failed")
            logger.exception("Confirmed ingest failed request_id=%s stage_id=%s", context.request_id, stage_id)
            self.audit.record(
                context,
                action="knowledge.confirm",
                outcome="failed",
                target_type="staged_upload",
                target_id=stage_id,
            )
            return f"I could not commit that upload. Reference `{context.request_id}`."

    def _status(self, job_id: str) -> str:
        job = self.incident_jobs.get(job_id)
        if not job:
            return f"I cannot find incident import `{job_id}`."
        status = job["status"]
        if status in {"pending", "running"}:
            return f"Incident import `{job_id}` is *{status}*."
        if status == "failed":
            return f"Incident import `{job_id}` failed: {job.get('error_message') or 'unknown error'}"
        metrics = job.get("metrics") or {}
        return (
            f"*Incident import {job_id}: succeeded*\n"
            f"• new {metrics.get('new', 0):,}; changed {metrics.get('changed', 0):,}; unchanged {metrics.get('unchanged', 0):,}\n"
            f"• vectors {metrics.get('vector_documents', 0):,}; graph +{metrics.get('graph_nodes_added', 0):,} nodes / +{metrics.get('graph_edges_added', 0):,} edges\n"
            f"• total {metrics.get('total_seconds', 0):.2f}s; embedding {metrics.get('embedding_documents_per_second', 0):.1f} docs/sec"
        )

    def _cancel(self, stage_id: str, context: RequestContext) -> str:
        staged = self.staging.get(stage_id)
        if not staged or staged["status"] != "staged":
            return f"`{stage_id}` is missing or no longer staged."
        if staged.get("uploader_id") and context.user_id != staged.get("uploader_id"):
            return "Only the person who staged this upload may cancel it."
        self.staging.set_status(stage_id, "cancelled")
        Path(staged["local_path"]).unlink(missing_ok=True)
        self.audit.record(
            context,
            action="knowledge.cancel",
            outcome="success",
            target_type="staged_upload",
            target_id=stage_id,
        )
        return f"Cancelled `{stage_id}`. It was not added to the knowledge base."

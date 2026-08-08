"""Knowledge Ingest Agent – staged, confirmed uploads from #knowledge-uploads."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.agents.base import BaseAgent
from src.core.audit import AuditStore
from src.core.config import settings
from src.core.context import RequestContext
from src.knowledge.file_loader import UploadValidationError, download_slack_file, extract_text
from src.knowledge.governed_ingest import commit_knowledge
from src.knowledge.incident_rag import IncidentRAG
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


class KnowledgeIngestAgent(BaseAgent):
    """Stages uploads and commits them only after explicit confirmation."""

    name = "knowledge_ingest"

    def __init__(self):
        self.staging = StagingStore()
        self.audit = AuditStore()

    async def handle(self, message: str, context: RequestContext) -> str:
        text = (message or "").strip()
        lower = text.lower()

        if lower.startswith("confirm "):
            return self._confirm(text.split(maxsplit=1)[1].strip(), context)
        if lower.startswith("cancel "):
            return self._cancel(text.split(maxsplit=1)[1].strip(), context)

        if not context.files:
            return (
                "Upload a supported file to stage it. Nothing becomes searchable until you confirm it.\n"
                "After staging, use `confirm <stage-id>` or `cancel <stage-id>`."
            )

        lines = ["*Upload staged — not yet searchable*"]
        for file_info in context.files:
            name = file_info.get("name") or file_info.get("id") or "unknown"
            try:
                local_path = await download_slack_file(file_info)
                extracted = extract_text(local_path)
                if not extracted.strip():
                    raise UploadValidationError("No extractable text found")
                stage_id = self.staging.create(
                    slack_file_id=str(file_info.get("id") or local_path.name),
                    file_name=str(name),
                    local_path=str(local_path),
                    uploader_id=context.user_id,
                    channel_id=context.channel_id,
                )
                self.audit.record(
                    context,
                    action="knowledge.stage",
                    outcome="success",
                    target_type="staged_upload",
                    target_id=stage_id,
                    metadata={"file_name": str(name)},
                )
                lines.append(
                    f"• *{name}* → `{stage_id}` ({len(extracted):,} extracted characters)\n"
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
            text = extract_text(path)
            result = commit_knowledge(
                text=text,
                title=staged["file_name"],
                source_id=staged["slack_file_id"],
                owner_id=context.user_id,
            )

            incident_note = ""
            if _looks_like_incident_csv(path):
                incidents = load_incidents_from_csv(path)
                settings.incidents_path.mkdir(parents=True, exist_ok=True)
                dest = settings.incidents_path / path.name
                if path.resolve() != dest.resolve():
                    shutil.copy2(path, dest)
                index_result = IncidentRAG().index_incidents(incidents)
                incident_note = f"; {index_result['indexed']} incident(s) indexed"

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
                return f"Confirmed `{stage_id}`. Content is unchanged, so no duplicate chunks were created{incident_note}."
            return f"Confirmed `{stage_id}` → {result['chunks']} searchable chunk(s){incident_note}."
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

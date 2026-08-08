"""Knowledge Ingest Agent – handles uploads from #knowledge-uploads."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from src.agents.base import BaseAgent
from src.core.config import settings
from src.knowledge.file_loader import download_slack_file, extract_text
from src.knowledge.incident_rag import IncidentRAG
from src.knowledge.ingest import ingest_text
from src.knowledge.vectorstore import VectorStore
from src.reporting.incidents import load_incidents_from_csv

logger = logging.getLogger(__name__)


def _looks_like_incident_csv(path: Path) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    try:
        incidents = load_incidents_from_csv(path)
        return len(incidents) > 0
    except Exception:
        return False


class KnowledgeIngestAgent(BaseAgent):
    """
    Processes uploaded documents and adds them to the hybrid knowledge base.

    Incident CSVs are also indexed into the dedicated incident RAG collection.
    """

    name = "knowledge_ingest"

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        files = context.get("files") or []

        if not files:
            return (
                "Please upload a file to add it to the knowledge base.\n\n"
                "Supported formats: Markdown, TXT, CSV, LOG, JSON, YAML "
                "(PDF and DOCX supported if the optional packages are installed).\n"
                "Incident export CSVs are also indexed for similar-ticket search."
            )

        results = []
        errors = []

        for file_info in files:
            name = file_info.get("name") or file_info.get("id") or "unknown"
            try:
                local_path = await download_slack_file(file_info)

                # Incident CSV path → dedicated incident RAG + copy to incidents drop-zone
                if _looks_like_incident_csv(local_path):
                    incidents = load_incidents_from_csv(local_path)
                    settings.incidents_path.mkdir(parents=True, exist_ok=True)
                    dest = settings.incidents_path / local_path.name
                    if local_path.resolve() != dest.resolve():
                        shutil.copy2(local_path, dest)

                    index_result = IncidentRAG().index_incidents(incidents)
                    results.append(
                        f"• *{name}* → incident RAG: {index_result['indexed']} ticket(s) indexed "
                        f"(collection total: {index_result['collection_total']})"
                    )
                    logger.info("Incident CSV indexed: %s → %s", name, index_result)
                    # Also keep a text form in general knowledge for keyword/theme search
                    text = extract_text(local_path)
                    if text.strip():
                        summary = ingest_text(
                            text=text,
                            source=name,
                            metadata={
                                "doc_type": "incident_export",
                                "slack_file_id": file_info.get("id"),
                                "uploaded_by": context.get("user"),
                            },
                        )
                        results.append(
                            f"  ↳ also added to knowledge base: {summary['chunks']} chunk(s)"
                        )
                    continue

                # Normal document path
                text = extract_text(local_path)
                if not text or not text.strip():
                    errors.append(f"• *{name}*: no extractable text found")
                    continue

                summary = ingest_text(
                    text=text,
                    source=name,
                    metadata={
                        "slack_file_id": file_info.get("id"),
                        "uploaded_by": context.get("user"),
                        "channel_id": context.get("channel_id"),
                        "local_path": str(local_path),
                    },
                )

                results.append(
                    f"• *{name}* → {summary['chunks']} chunk(s), "
                    f"{summary['entities']} entit(y/ies) indexed"
                )
                logger.info("Ingested %s: %s", name, summary)

            except Exception as e:
                logger.exception("Failed to ingest file %s", name)
                errors.append(f"• *{name}*: `{type(e).__name__}: {e}`")

        lines = ["*Knowledge Ingest complete*"]

        if results:
            lines.append("")
            lines.append("Successfully added:")
            lines.extend(results)

        if errors:
            lines.append("")
            lines.append("Issues:")
            lines.extend(errors)

        try:
            total = VectorStore().count()
            lines.append("")
            lines.append(f"_General knowledge chunks: {total}_")
        except Exception:
            pass

        return "\n".join(lines)

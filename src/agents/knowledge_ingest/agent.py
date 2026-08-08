"""Knowledge Ingest Agent – handles uploads from #knowledge-uploads."""

from __future__ import annotations

import logging
from typing import Any

from src.agents.base import BaseAgent
from src.knowledge.file_loader import download_slack_file, extract_text
from src.knowledge.ingest import ingest_text
from src.knowledge.vectorstore import VectorStore

logger = logging.getLogger(__name__)


class KnowledgeIngestAgent(BaseAgent):
    """
    Processes uploaded documents and adds them to the hybrid knowledge base
    (Vector store + Graph store).
    """

    name = "knowledge_ingest"

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        files = context.get("files") or []

        if not files:
            return (
                "Please upload a file to add it to the knowledge base.\n\n"
                "Supported formats: Markdown, TXT, CSV, LOG, JSON, YAML "
                "(PDF and DOCX supported if the optional packages are installed)."
            )

        results = []
        errors = []

        for file_info in files:
            name = file_info.get("name") or file_info.get("id") or "unknown"
            try:
                # 1. Download from Slack → local raw folder
                local_path = await download_slack_file(file_info)

                # 2. Extract text
                text = extract_text(local_path)
                if not text or not text.strip():
                    errors.append(f"• *{name}*: no extractable text found")
                    continue

                # 3. Ingest into hybrid RAG (vector + graph)
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

        # Build response
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
            lines.append(f"_Total chunks now in knowledge base: {total}_")
        except Exception:
            pass

        return "\n".join(lines)

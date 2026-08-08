"""Knowledge Ingest Agent – handles uploads from #knowledge-uploads."""

from typing import Any

from src.agents.base import BaseAgent


class KnowledgeIngestAgent(BaseAgent):
    """Processes uploaded documents and adds them to the vector store."""

    name = "knowledge_ingest"

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        files = context.get("files", [])
        if not files:
            return "Please upload a file (PDF, Markdown, CSV, etc.) to add it to the knowledge base."

        # TODO: Download file, chunk, embed, store in vector DB
        names = [f.get("name", "unknown") for f in files]
        return (
            "Knowledge Ingest Agent received file(s):\n"
            + "\n".join(f"• {n}" for n in names)
            + "\n\nIngest pipeline will be implemented next."
        )

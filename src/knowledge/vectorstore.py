"""Local vector store using Chroma."""

from pathlib import Path

from src.core.config import settings


class VectorStore:
    """Thin wrapper around Chroma for knowledge retrieval."""

    def __init__(self, path: Path | None = None):
        self.path = path or settings.vectorstore_path
        self.path.mkdir(parents=True, exist_ok=True)
        # TODO: Initialise Chroma client and collection

    def add_documents(self, documents: list[str], metadatas: list[dict] | None = None):
        """Add documents to the vector store."""
        raise NotImplementedError("Ingest pipeline coming next")

    def similarity_search(self, query: str, k: int = 5) -> list[dict]:
        """Retrieve the most relevant document chunks."""
        raise NotImplementedError("Retrieval coming next")

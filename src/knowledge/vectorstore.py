"""Local vector store using Chroma."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_core.documents import Document

from src.core.config import settings
from src.knowledge.embeddings import get_embeddings

logger = logging.getLogger(__name__)


class VectorStore:
    """Persistent Chroma collection for document chunks."""

    def __init__(
        self,
        path: Path | None = None,
        collection_name: str = "knowledge",
        embedding_purpose: str = "general",
    ):
        self.path = path or settings.vectorstore_path
        self.path.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self.embedding_purpose = embedding_purpose

        self._client = chromadb.PersistentClient(
            path=str(self.path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._embeddings = get_embeddings(purpose=embedding_purpose)

    def add_documents(
        self,
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
        batch_size: int | None = None,
    ) -> list[str]:
        """Embed and add documents in batches (important for large incident CSVs)."""
        if not documents:
            return []

        metadatas = metadatas or [{} for _ in documents]
        if ids is None:
            import uuid

            ids = [str(uuid.uuid4()) for _ in documents]

        batch_size = batch_size or (
            settings.incident_embedding_batch_size
            if self.embedding_purpose == "incident"
            else 128
        )
        batch_size = max(1, int(batch_size))

        total = len(documents)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch_docs = documents[start:end]
            batch_meta = metadatas[start:end]
            batch_ids = ids[start:end]

            embeddings = self._embeddings.embed_documents(batch_docs)
            self._collection.add(
                documents=batch_docs,
                embeddings=embeddings,
                metadatas=batch_meta,
                ids=batch_ids,
            )
            logger.info(
                "Embedded batch %s–%s / %s into collection '%s'",
                start + 1,
                end,
                total,
                self.collection_name,
            )

        return ids

    def similarity_search(
        self,
        query: str,
        k: int = 5,
        where: dict | None = None,
    ) -> list[Document]:
        """Return the top-k most similar document chunks."""
        query_embedding = self._embeddings.embed_query(query)

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        documents: list[Document] = []
        if not results["documents"]:
            return documents

        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            metadata = dict(meta or {})
            metadata["score"] = 1.0 - float(dist)
            documents.append(Document(page_content=doc, metadata=metadata))

        return documents

    def count(self) -> int:
        return self._collection.count()

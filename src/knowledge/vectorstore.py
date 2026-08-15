"""Local vector store using Chroma behind a small repository interface."""

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
        """Embed and upsert documents in batches."""
        if not documents:
            return []
        metadatas = metadatas or [{} for _ in documents]
        if ids is None:
            import uuid

            ids = [str(uuid.uuid4()) for _ in documents]
        batch_size = batch_size or (
            settings.incident_embedding_batch_size if self.embedding_purpose == "incident" else 128
        )
        batch_size = max(1, int(batch_size))
        total = len(documents)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch_docs = documents[start:end]
            embeddings = self._embeddings.embed_documents(batch_docs)
            self._collection.upsert(
                documents=batch_docs,
                embeddings=embeddings,
                metadatas=metadatas[start:end],
                ids=ids[start:end],
            )
            logger.info(
                "Embedded batch %s–%s / %s into '%s'",
                start + 1,
                end,
                total,
                self.collection_name,
            )
        return ids

    def update_metadatas(
        self,
        ids: list[str],
        metadatas: list[dict[str, Any]],
        *,
        batch_size: int = 1000,
    ) -> None:
        """Refresh metadata without recomputing embeddings, in bounded batches.

        Missing IDs are ignored by Chroma, which is useful during schema migrations
        where a historical incident may not have every focused field document.
        Batching also keeps large historical replays below Chroma's maximum batch
        limits.
        """
        if not ids:
            return
        batch_size = max(1, int(batch_size))
        total = len(ids)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            self._collection.update(
                ids=ids[start:end],
                metadatas=metadatas[start:end],
            )

    def delete_documents(self, ids: list[str]) -> None:
        if ids:
            self._collection.delete(ids=ids)

    def list_ids(self) -> list[str]:
        """Return collection IDs without loading documents or embeddings."""
        result = self._collection.get(include=[])
        return [str(value) for value in (result.get("ids") or [])]

    def similarity_search(
        self,
        query: str,
        k: int = 5,
        where: dict | None = None,
    ) -> list[Document]:
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
        for doc, meta, dist, doc_id in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
            results["ids"][0],
        ):
            metadata = dict(meta or {})
            metadata.setdefault("chunk_id", doc_id)
            metadata["score"] = 1.0 - float(dist)
            metadata["semantic_score"] = metadata["score"]
            documents.append(Document(page_content=doc, metadata=metadata))
        return documents

    def all_documents(self, where: dict | None = None) -> list[Document]:
        """Return indexed chunks for local lexical ranking.

        Chroma remains a rebuildable index; this method exists so the first hybrid
        retrieval implementation does not require another external search service.
        """
        result = self._collection.get(where=where, include=["documents", "metadatas"])
        documents: list[Document] = []
        for doc_id, doc, meta in zip(
            result.get("ids") or [],
            result.get("documents") or [],
            result.get("metadatas") or [],
        ):
            metadata = dict(meta or {})
            metadata.setdefault("chunk_id", doc_id)
            documents.append(Document(page_content=doc or "", metadata=metadata))
        return documents

    def count(self) -> int:
        return self._collection.count()

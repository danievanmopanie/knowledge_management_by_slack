"""Confirmed, version-aware knowledge ingestion."""

from __future__ import annotations

from typing import Any

from src.knowledge.catalog import KnowledgeCatalog
from src.knowledge.ingest import chunk_text
from src.knowledge.vectorstore import VectorStore


def commit_knowledge(
    *,
    text: str,
    title: str,
    source_id: str,
    owner_id: str | None,
    visibility: str = "internal",
    allowed_user_ids: str = "",
    allowed_channel_ids: str = "",
    catalog: KnowledgeCatalog | None = None,
    vector_store: VectorStore | None = None,
) -> dict[str, Any]:
    """Commit one confirmed document and replace only its previous active chunks."""
    catalog = catalog or KnowledgeCatalog()
    vector_store = vector_store or VectorStore()
    version = catalog.register_version(
        source_system="slack",
        source_id=source_id,
        title=title,
        text=text,
        owner_id=owner_id,
        visibility=visibility,
    )
    if version["unchanged"]:
        return {**version, "chunks": len(version.get("old_chunk_ids", []))}

    old_ids = version.get("old_chunk_ids", [])
    vector_store.delete_documents(old_ids)

    chunks = chunk_text(text)
    ids = [
        f"{version['document_id']}:{version['content_hash'][:12]}:{index:04d}"
        for index, _ in enumerate(chunks)
    ]
    metadatas = [
        {
            "source": title,
            "document_id": version["document_id"],
            "version_id": version["version_id"],
            "content_hash": version["content_hash"],
            "chunk_index": index,
            "visibility": visibility,
            "allowed_user_ids": allowed_user_ids,
            "allowed_channel_ids": allowed_channel_ids,
        }
        for index, _ in enumerate(chunks)
    ]
    vector_store.add_documents(chunks, metadatas=metadatas, ids=ids)
    catalog.set_chunk_ids(version["version_id"], ids)
    return {**version, "chunks": len(ids), "chunk_ids": ids}

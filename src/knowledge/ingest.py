"""Document loading, chunking and ingest into Vector + Graph stores."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.core.config import settings
from src.knowledge.graphstore import GraphStore
from src.knowledge.vectorstore import VectorStore


def _simple_entity_hints(text: str) -> list[str]:
    """
    Very lightweight entity extraction for the graph layer.
    Looks for common IT patterns (can be improved later with an LLM)."""
    patterns = [
        r"\b(?:floor|building|site)\s*[A-Z0-9-]+\b",
        r"\b(?:error|code|incident)[\s#:]*[A-Z0-9-]+\b",
        r"\b(?:printer|laptop|switch|ap|wifi|vpn|mfa)\b",
        r"\b(?:servicenow|intune|teams|outlook|cisco)\b",
    ]
    found = set()
    for pat in patterns:
        for match in re.finditer(pat, text, flags=re.IGNORECASE):
            found.add(match.group(0).strip().lower())
    return list(found)


def chunk_text(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 150,
) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_text(text)


def ingest_text(
    text: str,
    source: str,
    metadata: dict[str, Any] | None = None,
    vector_store: VectorStore | None = None,
    graph_store: GraphStore | None = None,
) -> dict[str, Any]:
    """
    Ingest a single document into both the vector store and the graph store.

    Returns a small summary of what was written.
    """
    vector_store = vector_store or VectorStore()
    graph_store = graph_store or GraphStore()
    metadata = metadata or {}

    chunks = chunk_text(text)
    if not chunks:
        return {"chunks": 0, "entities": 0}

    # Vector side
    metadatas = []
    for i, _ in enumerate(chunks):
        meta = {
            "source": source,
            "chunk_index": i,
            **metadata,
        }
        metadatas.append(meta)

    ids = vector_store.add_documents(chunks, metadatas=metadatas)

    # Graph side – extract simple entity hints and link them to the source
    entities = _simple_entity_hints(text)
    for ent in entities:
        graph_store.add_entity(ent, entity_type="concept", name=ent)
        graph_store.add_relation(ent, source, relation="mentioned_in")

    if entities:
        graph_store.save()

    return {
        "chunks": len(ids),
        "entities": len(entities),
        "source": source,
    }


def ingest_file(
    file_path: Path,
    source_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load a text-based file and ingest it."""
    path = Path(file_path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    source = source_name or path.name
    return ingest_text(text, source=source, metadata=metadata)

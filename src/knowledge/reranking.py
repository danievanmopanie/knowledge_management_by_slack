"""Deterministic evidence reranking and diversity controls."""

from __future__ import annotations

import re
from collections import defaultdict

from langchain_core.documents import Document

_SPACE = re.compile(r"\s+")


def _content_key(text: str) -> str:
    return _SPACE.sub(" ", (text or "").strip().lower())[:500]


def _score(doc: Document) -> float:
    metadata = doc.metadata or {}
    fusion = float(metadata.get("fusion_score") or metadata.get("score") or 0.0)
    semantic = max(0.0, min(1.0, float(metadata.get("semantic_score") or 0.0)))
    lexical_raw = max(0.0, float(metadata.get("lexical_score") or 0.0))
    lexical = lexical_raw / (lexical_raw + 5.0) if lexical_raw else 0.0
    # Fusion contributes ordering information; semantic/lexical retain interpretable strength.
    return fusion + semantic * 0.55 + lexical * 0.35


def rerank_documents(
    documents: list[Document],
    *,
    limit: int,
    max_per_document: int = 2,
) -> list[Document]:
    """Remove duplicate evidence and prevent a single document dominating context."""
    scored: list[tuple[float, int, Document]] = []
    seen_content: set[str] = set()
    for index, doc in enumerate(documents):
        key = _content_key(doc.page_content)
        if not key or key in seen_content:
            continue
        seen_content.add(key)
        metadata = dict(doc.metadata or {})
        metadata["rerank_score"] = _score(doc)
        scored.append(
            (
                metadata["rerank_score"],
                index,
                Document(page_content=doc.page_content, metadata=metadata),
            )
        )

    scored.sort(key=lambda item: (-item[0], item[1]))
    per_document: defaultdict[str, int] = defaultdict(int)
    selected: list[Document] = []
    for _, _, doc in scored:
        document_id = str(doc.metadata.get("document_id") or doc.metadata.get("source") or "unknown")
        if per_document[document_id] >= max_per_document:
            continue
        per_document[document_id] += 1
        selected.append(doc)
        if len(selected) >= limit:
            break
    return selected

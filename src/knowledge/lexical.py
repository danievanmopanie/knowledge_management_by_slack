"""Lightweight lexical ranking and reciprocal-rank fusion."""

from __future__ import annotations

import math
import re
from collections import Counter

from langchain_core.documents import Document

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN.findall(text or "")]


def lexical_search(
    query: str,
    documents: list[Document],
    *,
    limit: int = 20,
    exact_terms: tuple[str, ...] = (),
) -> list[Document]:
    """Rank documents with a compact BM25-style score plus exact-term boosts."""
    if not documents:
        return []
    query_tokens = tokenize(query)
    if not query_tokens and not exact_terms:
        return []

    tokenized = [tokenize(doc.page_content) for doc in documents]
    lengths = [max(len(tokens), 1) for tokens in tokenized]
    avg_len = sum(lengths) / max(len(lengths), 1)
    df: Counter[str] = Counter()
    for tokens in tokenized:
        df.update(set(tokens))

    n_docs = len(documents)
    k1 = 1.5
    b = 0.75
    scored: list[tuple[float, int, Document]] = []
    exact_lower = tuple(term.lower() for term in exact_terms)

    for index, (doc, tokens, doc_len) in enumerate(zip(documents, tokenized, lengths)):
        tf = Counter(tokens)
        score = 0.0
        for term in query_tokens:
            freq = tf.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            denom = freq + k1 * (1 - b + b * doc_len / avg_len)
            score += idf * (freq * (k1 + 1)) / denom

        haystack = (doc.page_content + " " + " ".join(str(v) for v in doc.metadata.values())).lower()
        exact_hits = sum(1 for term in exact_lower if term and term in haystack)
        score += exact_hits * 4.0
        if score <= 0:
            continue
        metadata = dict(doc.metadata or {})
        metadata["lexical_score"] = score
        scored.append((score, index, Document(page_content=doc.page_content, metadata=metadata)))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [doc for _, _, doc in scored[:limit]]


def reciprocal_rank_fusion(
    semantic: list[Document],
    lexical: list[Document],
    *,
    limit: int,
    rank_constant: int = 60,
) -> list[Document]:
    """Fuse semantic and lexical rankings deterministically using RRF."""
    by_id: dict[str, Document] = {}
    scores: dict[str, float] = {}

    def consume(documents: list[Document], source: str) -> None:
        for rank, doc in enumerate(documents, 1):
            metadata = dict(doc.metadata or {})
            evidence_id = str(
                metadata.get("chunk_id")
                or metadata.get("id")
                or metadata.get("version_id")
                or metadata.get("source")
                or f"{source}-{rank}"
            )
            by_id.setdefault(evidence_id, Document(page_content=doc.page_content, metadata=metadata))
            scores[evidence_id] = scores.get(evidence_id, 0.0) + 1.0 / (rank_constant + rank)
            if source == "semantic" and metadata.get("semantic_score") is not None:
                by_id[evidence_id].metadata["semantic_score"] = metadata["semantic_score"]
            if source == "lexical" and metadata.get("lexical_score") is not None:
                by_id[evidence_id].metadata["lexical_score"] = metadata["lexical_score"]

    consume(semantic, "semantic")
    consume(lexical, "lexical")

    ordered = sorted(scores, key=lambda evidence_id: (-scores[evidence_id], evidence_id))[:limit]
    fused: list[Document] = []
    for evidence_id in ordered:
        doc = by_id[evidence_id]
        doc.metadata["score"] = scores[evidence_id]
        doc.metadata["fusion_score"] = scores[evidence_id]
        doc.metadata.setdefault("chunk_id", evidence_id)
        fused.append(doc)
    return fused

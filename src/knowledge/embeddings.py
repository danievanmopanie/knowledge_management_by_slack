"""Local embeddings using an OpenAI-compatible endpoint (Ollama on GX10).

Supports:
- General knowledge embeddings
- Dedicated incident embeddings (often a stronger retrieval model)
- Optional query/document prefixes used by models such as nomic / e5-style embedders
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import OpenAIEmbeddings

from src.core.config import settings


class PrefixedEmbeddings:
    """
    Thin wrapper that applies asymmetric prefixes before embedding.

    Many strong retrieval models improve when documents and queries are
    prefixed differently, e.g.:
      document: "search_document: ..."
      query:    "search_query: ..."
    """

    def __init__(
        self,
        base: OpenAIEmbeddings,
        document_prefix: str = "",
        query_prefix: str = "",
    ):
        self._base = base
        self.document_prefix = document_prefix or ""
        self.query_prefix = query_prefix or ""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.document_prefix:
            texts = [f"{self.document_prefix}{t}" for t in texts]
        return self._base.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        if self.query_prefix:
            text = f"{self.query_prefix}{text}"
        return self._base.embed_query(text)


def _make_openai_embeddings(model: str, base_url: str | None = None) -> OpenAIEmbeddings:
    """Create an OpenAI-compatible embeddings client for Ollama.

    LangChain's OpenAIEmbeddings tokenises inputs by default when checking the
    embedding context length. That makes the OpenAI SDK send token arrays to
    compatible providers. Ollama is most reliable when it receives the raw
    text strings, so context-length tokenisation is deliberately disabled.
    Ollama handles over-length input truncation itself.

    ``encoding_format`` is an OpenAI API request option rather than a declared
    constructor field in the LangChain version used on the GX10. Supplying it
    through ``model_kwargs`` preserves the request while avoiding LangChain's
    warning about moving an unknown top-level parameter automatically.
    """
    return OpenAIEmbeddings(
        base_url=base_url or settings.embedding_base_url,
        api_key=settings.llm_api_key,
        model=model,
        check_embedding_ctx_length=False,
        model_kwargs={"encoding_format": "float"},
    )


@lru_cache(maxsize=4)
def get_embeddings(purpose: str = "general"):
    """
    Return an embeddings client.

    purpose:
      - "general"  → knowledge articles / runbooks
      - "incident" → incident tickets (defaults to a stronger retrieval model)
    """
    purpose = (purpose or "general").lower()

    if purpose == "incident":
        model = settings.incident_embedding_model or settings.embedding_model
        doc_prefix = settings.incident_embedding_document_prefix
        query_prefix = settings.incident_embedding_query_prefix
    else:
        model = settings.embedding_model
        doc_prefix = settings.embedding_document_prefix
        query_prefix = settings.embedding_query_prefix

    base = _make_openai_embeddings(model)

    if doc_prefix or query_prefix:
        return PrefixedEmbeddings(
            base,
            document_prefix=doc_prefix,
            query_prefix=query_prefix,
        )
    return base

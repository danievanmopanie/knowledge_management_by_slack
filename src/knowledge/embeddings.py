"""Local embeddings using an OpenAI-compatible endpoint (Ollama on GX10)."""

from langchain_openai import OpenAIEmbeddings

from src.core.config import settings


def get_embeddings() -> OpenAIEmbeddings:
    """Return an embeddings client pointed at the local model."""
    return OpenAIEmbeddings(
        base_url=settings.embedding_base_url,
        api_key=settings.llm_api_key,  # often "ollama" or any non-empty string
        model=settings.embedding_model,
    )

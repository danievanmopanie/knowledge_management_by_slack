"""OpenAI-compatible client for the local LLM running on the GX10."""

from langchain_openai import ChatOpenAI

from src.core.config import settings


def get_llm() -> ChatOpenAI:
    """Return a ChatOpenAI instance pointed at the local model."""
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=0.2,
    )

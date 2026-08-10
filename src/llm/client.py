"""OpenAI-compatible clients for local LLMs running on the GX10."""

from langchain_openai import ChatOpenAI

from src.core.config import settings


def get_llm() -> ChatOpenAI:
    """Return the stronger technician-facing response model."""
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=0.2,
    )


def get_support_extraction_llm() -> ChatOpenAI:
    """Return the smaller deterministic model used for batch knowledge extraction."""
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.support_extraction_model,
        temperature=settings.support_extraction_temperature,
    )

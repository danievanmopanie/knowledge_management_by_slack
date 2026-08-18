"""LLM-assisted revision of one existing governed knowledge article.

The expensive work is intentionally isolated here so Slack interactions can persist
and acknowledge work before Atlas is called.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from src.knowledge.vectorstore import VectorStore
from src.llm.client import get_llm

logger = logging.getLogger(__name__)

REVISION_SYSTEM_PROMPT = """You are a senior IT knowledge editor revising one existing
knowledge article from a technician's field correction.

Rules:
- Preserve content the correction does not contradict or extend.
- Apply only the specific correction supported by the current article and the note.
- Do not invent root causes, steps, products, or facts.
- If the note is too vague to apply safely, keep the article unchanged and add a short
  Reviewer note at the end explaining what still needs human clarification.
- Return the complete corrected article text, not commentary about the edit.
"""


def reconstruct_document_text(
    document_id: str,
    *,
    vector_store: VectorStore | None = None,
) -> str:
    """Rebuild current article text from indexed chunks in source order."""
    store = vector_store or VectorStore()
    docs = store.all_documents(where={"document_id": document_id})
    docs.sort(key=lambda doc: int((doc.metadata or {}).get("chunk_index", 0) or 0))
    return "\n\n".join(doc.page_content for doc in docs if doc.page_content).strip()


async def revise_article(*, current_text: str, edit_note: str, llm=None) -> str:
    """Ask the configured local LLM for one focused, grounded revision."""
    active_llm = llm or get_llm()
    response = await active_llm.ainvoke(
        [
            SystemMessage(content=REVISION_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Current article:\n{current_text or '(article text unavailable)'}\n\n"
                    f"Technician correction:\n{edit_note.strip()}"
                )
            ),
        ]
    )
    revised = response.content if hasattr(response, "content") else str(response)
    revised = str(revised).strip()
    if not revised:
        raise ValueError("Article revision model returned an empty draft")
    return revised

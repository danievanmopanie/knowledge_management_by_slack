"""LLM-assisted revision of an existing knowledge article from a technician's note.

This is the "reference/edit an existing article from #frontend-support" half of
knowledge-base onboarding: rather than a technician's correction turning into a
disconnected new article, it reconstructs the article's current text from its
indexed chunks and drafts a targeted revision grounded in that text plus the note.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from src.knowledge.vectorstore import VectorStore
from src.llm.client import get_llm

logger = logging.getLogger(__name__)

REVISION_SYSTEM_PROMPT = """You are a senior IT knowledge editor revising one existing
knowledge base article in response to a technician's field correction.

Rules:
- Preserve everything in the current article that the correction note does not
  contradict or extend. This is an edit, not a rewrite.
- Apply only the specific change the note describes. Do not invent additional facts,
  root causes, or steps that are not supported by the current article or the note.
- If the note is too vague to apply with confidence, leave the article body unchanged
  and add a single "## Reviewer note" section at the end quoting the technician's note
  verbatim, so a human finishes the edit instead of the model guessing.
- Return the full corrected article text (not just the changed part or a diff), in the
  same general structure as the current article.
"""


def reconstruct_document_text(document_id: str, *, vector_store: VectorStore | None = None) -> str:
    """Rebuild a document's current text from its indexed chunks, in original order."""
    store = vector_store or VectorStore()
    docs = store.all_documents(where={"document_id": document_id})
    docs.sort(key=lambda doc: int((doc.metadata or {}).get("chunk_index", 0) or 0))
    return "\n\n".join(doc.page_content for doc in docs if doc.page_content)


async def revise_article(*, current_text: str, edit_note: str, llm=None) -> str:
    """Draft a revised article, falling back to the original text plus a reviewer note on failure."""
    messages = [
        SystemMessage(content=REVISION_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Current article:\n{current_text or '(no existing text could be reconstructed)'}\n\n"
                f"Technician's correction note:\n{edit_note}"
            )
        ),
    ]
    try:
        active_llm = llm or get_llm()
        response = await active_llm.ainvoke(messages)
        revised = response.content if hasattr(response, "content") else str(response)
        revised = revised.strip()
        if not revised:
            raise ValueError("Empty revision")
        return revised
    except Exception:
        logger.exception("Article revision failed; keeping the original text with a reviewer note")
        base = current_text.rstrip() if current_text.strip() else "(no existing text could be reconstructed)"
        return base + f"\n\n## Reviewer note\n{edit_note}\n"

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

REVIEW_FEEDBACK_SYSTEM_PROMPT = """You are a senior IT knowledge editor updating an
already-proposed knowledge article revision using explicit technical reviewer feedback.

Rules:
- Treat the current proposed revision as the starting draft.
- Apply only feedback explicitly supplied by named reviewers.
- Preserve draft content that the feedback does not contradict or extend.
- Do not invent technical facts, validation results, root causes, products, or steps.
- If reviewer inputs conflict, do not choose silently. Preserve the safer existing text
  and add a short Reviewer note identifying the conflict for human resolution.
- Return the complete updated article text, not commentary about your editing process.
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


async def apply_review_feedback(
    *,
    proposed_text: str,
    reviewer_feedback: list[dict],
    llm=None,
) -> str:
    """Update a proposed draft only after the owner explicitly applies completed reviews."""
    if not proposed_text.strip():
        raise ValueError("Cannot apply review feedback without an existing proposed draft")
    feedback_lines: list[str] = []
    for review in reviewer_feedback:
        response_note = str(review.get("response_note") or "").strip()
        if not response_note:
            continue
        reviewer = str(review.get("reviewer_user_id") or "unknown")
        requested = str(review.get("review_note") or "").strip()
        prefix = f"Reviewer {reviewer}"
        if requested:
            prefix += f" (asked to review: {requested})"
        feedback_lines.append(f"- {prefix}: {response_note}")
    if not feedback_lines:
        raise ValueError("No completed technical review input is available to apply")

    active_llm = llm or get_llm()
    response = await active_llm.ainvoke(
        [
            SystemMessage(content=REVIEW_FEEDBACK_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Current proposed revision:\n{proposed_text.strip()}\n\n"
                    "Completed technical reviewer feedback:\n"
                    + "\n".join(feedback_lines)
                )
            ),
        ]
    )
    revised = response.content if hasattr(response, "content") else str(response)
    revised = str(revised).strip()
    if not revised:
        raise ValueError("Review-feedback revision model returned an empty draft")
    return revised

"""Generative drafting of formal knowledge articles from confirmed field evidence.

This is the piece that turns a raw confirmed Slack resolution into something a
technician would actually want to read later: a structured, well-written KB
article instead of a bare symptom/resolution sentence pair. The model is
instructed to draft only from supplied evidence and to say so plainly when a
section (most often root cause) is not actually supported by the evidence,
rather than invent one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.client import get_llm

logger = logging.getLogger(__name__)

DRAFT_SYSTEM_PROMPT = """You are a senior IT knowledge editor. Turn one confirmed field
resolution into a concise, high-quality formal knowledge base article that a different
technician could follow without having seen the original incident.

Rules:
- Use ONLY the facts supplied below. Never invent a root cause, a validation step, a
  product detail, or an environment that is not supported by the evidence.
- If the root cause is not clearly evidenced, write "Root cause not confirmed" under
  that heading instead of guessing.
- Structure the article with exactly these Markdown headings, in this order, omitting
  a heading's body only if there is truly nothing evidenced to put under it:
  # <short, specific title naming the symptom and technology>
  ## Symptom
  ## Environment
  ## Root cause
  ## Resolution steps
  ## Validation
  ## Related incidents
- Resolution steps must be a numbered, actionable checklist.
- Keep it tight: this is a reference article, not a narrative. No filler sentences,
  no restating the same fact twice, no meta-commentary about being an AI.
"""


@dataclass(frozen=True)
class DraftedArticle:
    title: str
    markdown: str


def _extract_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            return title or fallback
    return fallback


def _fallback_markdown(title: str, symptom: str, resolution: str) -> str:
    return (
        f"# {title}\n\n"
        f"## Symptom\n{symptom or 'Not captured'}\n\n"
        "## Root cause\nRoot cause not confirmed\n\n"
        f"## Resolution steps\n1. {resolution or 'Not captured'}\n"
    )


async def draft_knowledge_article(
    *,
    symptom: str,
    resolution: str,
    environment: str = "",
    actions_tried: str = "",
    contributors: list[str] | None = None,
    related_incidents: list[str] | None = None,
    fallback_title: str = "Untitled knowledge article",
    llm=None,
) -> DraftedArticle:
    """Draft a structured Markdown KB article from one confirmed field resolution.

    Falls back to a minimal, honestly-labelled skeleton (rather than raising) so a
    drafting failure never blocks a confirmed fix from being routed for human review.
    """
    evidence = [
        f"Confirmed symptom: {symptom or 'not captured'}",
        f"Confirmed resolution: {resolution or 'not captured'}",
    ]
    if environment:
        evidence.append(f"Environment: {environment}")
    if actions_tried:
        evidence.append(f"Actions tried in the field before the fix: {actions_tried}")
    if contributors:
        evidence.append(f"Contributors: {', '.join(contributors)}")
    if related_incidents:
        evidence.append(f"Related past incidents: {', '.join(related_incidents)}")

    messages = [
        SystemMessage(content=DRAFT_SYSTEM_PROMPT),
        HumanMessage(content="\n".join(evidence)),
    ]

    try:
        active_llm = llm or get_llm()
        response = await active_llm.ainvoke(messages)
        markdown = response.content if hasattr(response, "content") else str(response)
        markdown = markdown.strip()
        if not markdown:
            raise ValueError("Empty draft")
    except Exception:
        logger.exception("Knowledge article drafting failed; using fallback skeleton")
        markdown = _fallback_markdown(fallback_title, symptom, resolution)

    title = _extract_title(markdown, fallback_title)
    return DraftedArticle(title=title, markdown=markdown)

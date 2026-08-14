"""Afternoon Frontend Support handover report."""

from __future__ import annotations

import logging
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from src.core.config import settings
from src.knowledge.retriever import HybridRetriever
from src.llm.client import get_llm
from src.reporting.operations import build_snapshot, render_operations_digest

logger = logging.getLogger(__name__)

AFTERNOON_SYSTEM_PROMPT = """You are preparing a practical afternoon handover for an IT Frontend Support team.

The message must help technicians understand what changed during the day and what must not be dropped.

Requirements:
- Sound like a concise shift/team handover, not a management report
- Start with meaningful changes or emerging patterns
- Highlight unresolved clusters, aging work and tickets with no recent movement
- Compare locations and assignment groups when another team may have useful experience
- Mention likely repeat patterns only when supported by the supplied data/evidence
- Call out where formal knowledge may help, but do not invent a knowledge article
- Do not shame or rank individual technicians
- End with a short "Carry forward" section containing the few items that need attention next
- Distinguish observed facts from inference
"""


async def generate_afternoon_handover_report(
    hours: int | None = None,
    use_knowledge: bool = True,
) -> str:
    window_hours = hours or settings.report_afternoon_hours
    snapshot = build_snapshot(
        window_hours=window_hours,
        aging_days=settings.report_aging_days,
        stale_hours=settings.report_stale_hours,
    )
    digest = render_operations_digest(snapshot)

    knowledge_context = ""
    if use_knowledge:
        try:
            result = HybridRetriever().retrieve(
                "frontend support repeat incidents known fixes handover unresolved themes",
                k=6,
            )
            knowledge_context = result.to_context_string(max_chars=3500)
        except Exception:
            logger.exception("Knowledge retrieval failed for afternoon support report")

    now_local = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f"Report date/time: {now_local}",
        f"Day activity window: last {window_hours} hours",
        f"Aging threshold: {settings.report_aging_days} days",
        f"Stale threshold: {settings.report_stale_hours} hours",
        "",
        digest,
    ]
    if knowledge_context:
        parts.extend(["", "### Related governed knowledge", knowledge_context])
    parts.extend(
        [
            "",
            "Write the afternoon Frontend Support handover now. Focus on what changed and what carries forward.",
        ]
    )

    try:
        response = await get_llm().ainvoke(
            [
                SystemMessage(content=AFTERNOON_SYSTEM_PROMPT),
                HumanMessage(content="\n".join(parts)),
            ]
        )
        body = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        logger.exception("LLM failed generating afternoon handover")
        body = (
            "*Afternoon Support Handover* (fallback – LLM unavailable)\n\n"
            f"{digest}\n\n"
            f"_Generation error: {type(exc).__name__}: {exc}_"
        )

    header = (
        "*Afternoon Frontend Support Handover*\n"
        f"_{now_local} · {len(snapshot.open_incidents)} open · "
        f"{len(snapshot.aging_incidents)} aging · {len(snapshot.stale_incidents)} stale_\n\n"
    )
    return header + body.strip()

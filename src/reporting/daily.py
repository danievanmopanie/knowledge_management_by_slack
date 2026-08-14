"""Morning Frontend Support operational focus report (07:00)."""

from __future__ import annotations

import logging
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from src.core.config import settings
from src.knowledge.retriever import HybridRetriever
from src.llm.client import get_llm
from src.reporting.operations import build_snapshot, render_operations_digest

logger = logging.getLogger(__name__)

DAILY_SYSTEM_PROMPT = """You are a highly practical operations lead for an IT Frontend Support team.

Produce a short morning *Focus of the Day* briefing that helps technicians decide where to spend attention.

Requirements:
- Sound like a useful team lead in Slack, not a dashboard dump
- Lead with the 2-4 things that actually matter today
- Highlight emerging themes and repeated symptoms from recent activity
- Call out aging and stale open work that needs movement
- Compare assignment groups and locations when that cross-team view is useful
- Surface evidence that another location/group may already have encountered the same pattern
- Do not shame individuals or turn the report into a performance leaderboard
- Distinguish facts in the incident data from inference
- Be concise and actionable
- If data is thin or incomplete, say so

Use short natural sections. Avoid restating every count supplied to you.
"""


async def generate_daily_focus_report(
    hours: int = 24,
    use_knowledge: bool = True,
) -> str:
    """Generate a morning operational intelligence brief for #frontend-support."""
    snapshot = build_snapshot(
        window_hours=hours,
        aging_days=settings.report_aging_days,
        stale_hours=settings.report_stale_hours,
    )
    digest = render_operations_digest(snapshot)

    knowledge_context = ""
    if use_knowledge:
        try:
            result = HybridRetriever().retrieve(
                "recurring frontend support incidents recent themes known fixes operational priorities",
                k=6,
            )
            knowledge_context = result.to_context_string(max_chars=3500)
        except Exception:
            logger.exception("Knowledge retrieval failed for morning support report")

    now_local = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_prompt_parts = [
        f"Report date/time: {now_local}",
        f"Recent-activity window: last {hours} hours",
        f"Aging threshold: {settings.report_aging_days} days",
        f"Stale threshold: {settings.report_stale_hours} hours without an update",
        "",
        digest,
    ]
    if knowledge_context:
        user_prompt_parts.extend(["", "### Related governed knowledge", knowledge_context])

    user_prompt_parts.extend(
        [
            "",
            "Write the morning Frontend Support focus briefing now. Prioritise insight over statistics.",
        ]
    )

    llm = get_llm()
    messages = [
        SystemMessage(content=DAILY_SYSTEM_PROMPT),
        HumanMessage(content="\n".join(user_prompt_parts)),
    ]

    try:
        response = await llm.ainvoke(messages)
        body = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:
        logger.exception("LLM failed generating morning report")
        body = (
            "*Morning Support Focus* (fallback – LLM unavailable)\n\n"
            f"{digest}\n\n"
            f"_Generation error: {type(exc).__name__}: {exc}_"
        )

    header = (
        "*Morning Frontend Support Pulse*\n"
        f"_{now_local} · {len(snapshot.open_incidents)} open · "
        f"{len(snapshot.aging_incidents)} aging · {len(snapshot.stale_incidents)} stale_\n\n"
    )
    return header + body.strip()

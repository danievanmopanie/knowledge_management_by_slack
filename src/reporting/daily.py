"""Daily Frontend Support focus report (07:00)."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.knowledge.retriever import HybridRetriever
from src.llm.client import get_llm
from src.reporting.incidents import (
    filter_incidents,
    load_all_incidents,
    recent_window,
)

logger = logging.getLogger(__name__)

DAILY_SYSTEM_PROMPT = """You are an operations lead for an IT Frontend Support team.

Produce a clear, practical *Focus of the Day* briefing for technicians starting their shift.

Requirements:
- Be concise and actionable
- Highlight the main themes from recent incidents
- Suggest what the team should watch for / prioritise today
- Call out any repeating patterns or high-impact issues
- Use plain language suitable for Slack
- If data is thin, say so and give best-effort guidance from available knowledge

Structure your reply with short sections and bullet points.
"""


def _incident_digest(incidents: list) -> str:
    if not incidents:
        return "No structured incident records found for the last 24 hours."

    lines = [f"Incidents in window: {len(incidents)}"]
    groups = Counter((i.assignment_group or "Unassigned") for i in incidents)
    categories = Counter((i.category or i.short_description[:40] or "Other") for i in incidents)
    states = Counter((i.state or "Unknown") for i in incidents)

    lines.append("\nBy assignment group:")
    for name, count in groups.most_common(8):
        lines.append(f"- {name}: {count}")

    lines.append("\nTop themes/categories:")
    for name, count in categories.most_common(8):
        lines.append(f"- {name}: {count}")

    lines.append("\nBy state:")
    for name, count in states.most_common():
        lines.append(f"- {name}: {count}")

    lines.append("\nSample recent incidents:")
    for inc in incidents[:12]:
        lines.append(
            f"- {inc.number}: {inc.short_description or '(no summary)'} "
            f"[{inc.assignment_group or 'no group'} | {inc.state or 'state?'}]"
        )

    return "\n".join(lines)


async def generate_daily_focus_report(
    hours: int = 24,
    use_knowledge: bool = True,
) -> str:
    """
    Generate the daily Focus of the Day report for #frontend-support.

    Combines:
    - Structured incident exports (CSV) when available
    - Hybrid RAG knowledge for context / themes
    - Local LLM synthesis aimed at technicians
    """
    since, until = recent_window(hours=hours)
    incidents = filter_incidents(load_all_incidents(), since=since, until=until)
    digest = _incident_digest(incidents)

    knowledge_context = ""
    if use_knowledge:
        try:
            retriever = HybridRetriever()
            # Query oriented toward operational themes
            result = retriever.retrieve(
                "common frontend support incidents themes troubleshooting priorities",
                k=6,
            )
            knowledge_context = result.to_context_string(max_chars=3500)
        except Exception:
            logger.exception("Knowledge retrieval failed for daily report")

    now_local = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_prompt_parts = [
        f"Report date/time: {now_local}",
        f"Window: last {hours} hours ({since.isoformat()} → {until.isoformat()})",
        "",
        "### Incident digest",
        digest,
    ]
    if knowledge_context:
        user_prompt_parts.extend(["", "### Related knowledge base context", knowledge_context])

    user_prompt_parts.extend(
        [
            "",
            "Write the Focus of the Day briefing for the Frontend Support technicians now.",
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
    except Exception as e:
        logger.exception("LLM failed generating daily report")
        body = (
            "*Daily Focus Report* (fallback – LLM unavailable)\n\n"
            f"{digest}\n\n"
            f"_Generation error: {type(e).__name__}: {e}_"
        )

    header = (
        f"*Daily Focus of the Day* — Frontend Support\n"
        f"_{now_local} · last {hours}h · {len(incidents)} incident(s) in data_\n\n"
    )
    return header + body.strip()

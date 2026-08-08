"""Weekly Frontend Support performance report."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.core.config import settings
from src.llm.client import get_llm
from src.reporting.incidents import (
    filter_incidents,
    last_week_window,
    load_all_incidents,
)

logger = logging.getLogger(__name__)

WEEKLY_SYSTEM_PROMPT = """You are an IT operations analyst producing a weekly Frontend Support performance briefing.

Focus on:
1. Overall weekly volume and outcomes
2. Assignment Groups that look problematic (high volume, open backlog, or concentration of issues)
3. People (callers) with recurring issues – potential systemic or training problems
4. Practical recommendations for the team lead / technicians

Be factual, specific, and useful in Slack. Use short sections and bullets.
If location filtering was applied, respect that scope in your wording.
If data is incomplete, state assumptions clearly.
"""


def _weekly_stats(incidents: list, location: str | None) -> str:
    if not incidents:
        scope = f" for location '{location}'" if location else ""
        return f"No structured incident records found for the last 7 days{scope}."

    lines = [f"Incidents in weekly window: {len(incidents)}"]
    if location:
        lines.append(f"Location filter: {location}")

    groups = Counter((i.assignment_group or "Unassigned") for i in incidents)
    callers = Counter((i.caller or "Unknown") for i in incidents if i.caller)
    states = Counter((i.state or "Unknown") for i in incidents)
    categories = Counter((i.category or "Uncategorised") for i in incidents)

    # Recurring callers: same person appearing multiple times
    recurring = [(name, count) for name, count in callers.most_common(20) if count >= 2]

    # Open vs closed rough split
    open_like = sum(
        1
        for i in incidents
        if (i.state or "").lower() not in {"resolved", "closed", "cancelled", "canceled"}
    )

    lines.append(f"Still open / not resolved (by state text): {open_like}")

    lines.append("\n### Assignment Groups (volume)")
    for name, count in groups.most_common(12):
        lines.append(f"- {name}: {count}")

    lines.append("\n### Categories")
    for name, count in categories.most_common(10):
        lines.append(f"- {name}: {count}")

    lines.append("\n### States")
    for name, count in states.most_common():
        lines.append(f"- {name}: {count}")

    lines.append("\n### People with recurring issues (≥2 tickets)")
    if recurring:
        for name, count in recurring[:15]:
            lines.append(f"- {name}: {count} incidents")
    else:
        lines.append("- None detected in this dataset")

    # Per-group sample of open issues for problematic groups
    lines.append("\n### Sample incidents by top groups")
    top_groups = {name for name, _ in groups.most_common(5)}
    by_group: dict[str, list] = defaultdict(list)
    for inc in incidents:
        g = inc.assignment_group or "Unassigned"
        if g in top_groups and len(by_group[g]) < 3:
            by_group[g].append(inc)
    for g, items in by_group.items():
        lines.append(f"{g}:")
        for inc in items:
            lines.append(
                f"  - {inc.number}: {inc.short_description or '(no summary)'} "
                f"[{inc.state or '?'} | caller={inc.caller or '?'}]"
            )

    return "\n".join(lines)


async def generate_weekly_performance_report(
    location: str | None = None,
) -> str:
    """
    Generate the weekly performance report for #frontend-support.

    `location` can be set via config (REPORT_WEEKLY_LOCATION) or passed explicitly.
    You indicated you will specify location later – leave unset until then.
    """
    location = location or getattr(settings, "report_weekly_location", None)
    since, until = last_week_window()
    incidents = filter_incidents(
        load_all_incidents(),
        since=since,
        until=until,
        location=location,
    )
    stats = _weekly_stats(incidents, location)

    now_local = datetime.now().strftime("%Y-%m-%d %H:%M")
    user_prompt = "\n".join(
        [
            f"Report generated: {now_local}",
            f"Window: last 7 days ({since.date()} → {until.date()})",
            f"Location filter: {location or 'ALL (not specified yet)'}",
            "",
            "### Weekly statistics",
            stats,
            "",
            "Write the weekly performance briefing now. Highlight problematic assignment groups "
            "and people with recurring issues. End with 3–5 concrete focus recommendations.",
        ]
    )

    llm = get_llm()
    messages = [
        SystemMessage(content=WEEKLY_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    try:
        response = await llm.ainvoke(messages)
        body = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.exception("LLM failed generating weekly report")
        body = (
            "*Weekly Performance Report* (fallback – LLM unavailable)\n\n"
            f"{stats}\n\n"
            f"_Generation error: {type(e).__name__}: {e}_"
        )

    scope = f" · location: {location}" if location else " · all locations"
    header = (
        f"*Weekly Frontend Support Performance*\n"
        f"_{now_local} · last 7 days{scope} · {len(incidents)} incident(s)_\n\n"
    )
    return header + body.strip()

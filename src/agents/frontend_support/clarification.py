"""Selective clarification engine for Frontend Support.

Clarification is deliberately lightweight: obvious missing discriminators are
identified before expensive retrieval/LLM work. Slack buttons improve response
quality, while ordinary free-text replies always remain valid.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.core.config import settings

MAX_CLARIFICATION_ROUNDS = 3
CLARIFICATION_ACTION = "frontend_clarification_select"


@dataclass(frozen=True)
class ClarificationOption:
    label: str
    value: str


@dataclass(frozen=True)
class ClarificationQuestion:
    key: str
    question: str
    options: tuple[ClarificationOption, ...]
    reason: str


class ClarificationStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.platform_db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS frontend_clarifications (
                    channel_id TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    round_number INTEGER NOT NULL DEFAULT 0,
                    pending_key TEXT,
                    answers_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (channel_id, thread_ts)
                )
                """
            )

    def state(self, channel_id: str, thread_ts: str) -> dict:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM frontend_clarifications WHERE channel_id=? AND thread_ts=?",
                (channel_id, thread_ts),
            ).fetchone()
        if not row:
            return {"round_number": 0, "pending_key": None, "answers": {}}
        return {
            "round_number": int(row["round_number"]),
            "pending_key": row["pending_key"],
            "answers": json.loads(row["answers_json"] or "{}"),
        }

    def ask(self, channel_id: str, thread_ts: str, key: str) -> int:
        state = self.state(channel_id, thread_ts)
        next_round = min(MAX_CLARIFICATION_ROUNDS, state["round_number"] + 1)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO frontend_clarifications
                    (channel_id, thread_ts, round_number, pending_key, answers_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, thread_ts) DO UPDATE SET
                    round_number=excluded.round_number,
                    pending_key=excluded.pending_key,
                    answers_json=excluded.answers_json
                """,
                (channel_id, thread_ts, next_round, key, json.dumps(state["answers"])),
            )
        return next_round

    def answer(self, channel_id: str, thread_ts: str, key: str, value: str) -> None:
        state = self.state(channel_id, thread_ts)
        answers = dict(state["answers"])
        answers[key] = value
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO frontend_clarifications
                    (channel_id, thread_ts, round_number, pending_key, answers_json)
                VALUES (?, ?, ?, NULL, ?)
                ON CONFLICT(channel_id, thread_ts) DO UPDATE SET
                    pending_key=NULL,
                    answers_json=excluded.answers_json
                """,
                (channel_id, thread_ts, state["round_number"], json.dumps(answers)),
            )

    def consume_free_text(self, channel_id: str, thread_ts: str, text: str) -> tuple[str, str] | None:
        state = self.state(channel_id, thread_ts)
        key = state.get("pending_key")
        if not key or not text.strip():
            return None
        self.answer(channel_id, thread_ts, key, text.strip())
        return key, text.strip()


class ClarificationEngine:
    """Identify only high-value ambiguity that materially changes support retrieval."""

    KNOWN_APPS = (
        "teams", "outlook", "sap", "excel", "word", "powerpoint", "onedrive",
        "sharepoint", "edge", "chrome", "servicenow", "citrix", "vpn",
    )

    def __init__(self, store: ClarificationStore | None = None):
        self.store = store or ClarificationStore()

    def next_question(self, channel_id: str, thread_ts: str, thread_text: str) -> ClarificationQuestion | None:
        state = self.store.state(channel_id, thread_ts)
        if state["pending_key"] or state["round_number"] >= MAX_CLARIFICATION_ROUNDS:
            return None
        answers = state["answers"]
        lowered = thread_text.lower()

        # Generic app failures are poor retrieval queries until the application
        # identity is known. Do not ask if the thread already names a known app.
        generic_app = bool(re.search(r"\b(app|application)\b", lowered))
        names_app = any(re.search(rf"\b{re.escape(app)}\b", lowered) for app in self.KNOWN_APPS)
        if generic_app and not names_app and "application" not in answers:
            return ClarificationQuestion(
                key="application",
                question="Which application is affected?",
                options=(
                    ClarificationOption("Microsoft Teams", "Microsoft Teams"),
                    ClarificationOption("Outlook", "Outlook"),
                    ClarificationOption("SAP", "SAP"),
                    ClarificationOption("Other / type it", "Other"),
                ),
                reason="The application name materially changes incident retrieval and troubleshooting.",
            )

        # Once an application is known, a timeout still benefits strongly from
        # knowing the failure stage. Ask only when the thread doesn't already say.
        if ("timeout" in lowered or "timing out" in lowered or "times out" in lowered) and "failure_stage" not in answers:
            stage_terms = ("opening", "launch", "sign in", "login", "during use", "call", "meeting", "after")
            if not any(term in lowered for term in stage_terms):
                return ClarificationQuestion(
                    key="failure_stage",
                    question="When does the timeout happen?",
                    options=(
                        ClarificationOption("Opening the app", "when opening the app"),
                        ClarificationOption("Signing in", "while signing in"),
                        ClarificationOption("During normal use", "during normal use"),
                        ClarificationOption("During calls / meetings", "during calls or meetings"),
                        ClarificationOption("Other / type it", "Other"),
                    ),
                    reason="The failure stage separates different incident patterns and likely causes.",
                )

        return None

    def blocks(self, question: ClarificationQuestion, channel_id: str, thread_ts: str) -> list[dict]:
        buttons = []
        for option in question.options:
            payload = json.dumps(
                {
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "key": question.key,
                    "value": option.value,
                    "label": option.label,
                }
            )
            buttons.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": option.label},
                    "action_id": CLARIFICATION_ACTION,
                    "value": payload,
                }
            )
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{question.question}*\n_{question.reason}_",
                },
            },
            {"type": "actions", "block_id": "frontend_clarification", "elements": buttons[:5]},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Choose an option, or just reply in the thread in your own words.",
                    }
                ],
            },
        ]

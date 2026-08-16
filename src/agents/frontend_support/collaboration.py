"""Collaborative thread intelligence for #frontend-support.

The channel is intentionally not a ticket queue. This module listens to ordinary
conversation, stores who contributed what, classifies messages cheaply, and only
asks the expensive support agent to intervene when the message is technically
meaningful. It also keeps private coaching memory isolated from public threads.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from src.core.config import settings
from src.knowledge.support_graph import (
    GraphEntity,
    SupportEntityType,
    SupportKnowledgeGraph,
    SupportRelation,
)

INCIDENT_RE = re.compile(r"\bINC\d{4,}\b", re.IGNORECASE)
RESOLUTION_PATTERNS = (
    re.compile(r"\bthat(?:'s| is)\s+(fixed|solved|resolved)\s+(it|the issue|the problem)?\b", re.I),
    re.compile(r"\b(that|this|it)\s+(fixed|solved|resolved)\s+(it|the issue|the problem)?\b", re.I),
    re.compile(r"\bworking\s+now\b", re.I),
    re.compile(r"\bissue\s+(is\s+)?resolved\b", re.I),
    re.compile(r"\bproblem\s+(is\s+)?resolved\b", re.I),
    re.compile(r"\bgot\s+it\s+working\b", re.I),
)
SUPPRESS_PATTERNS = (
    re.compile(r"\bwe(?:'ve| have)\s+got\s+this\b", re.I),
    re.compile(r"\bwe(?:'ll| will)\s+take\s+it\s+from\s+here\b", re.I),
    re.compile(r"\b(?:bot|assistant|ai)[, ]+stay\s+quiet\b", re.I),
    re.compile(r"\bno\s+more\s+suggestions\b", re.I),
)
RESUME_PATTERNS = (
    re.compile(r"\b(?:bot|assistant|ai)[, ]+(?:help|jump\s+back\s+in)\b", re.I),
    re.compile(r"\bneed\s+the\s+(?:bot|assistant|ai)\s+again\b", re.I),
)
TECHNICAL_HINTS = (
    "error",
    "issue",
    "problem",
    "not working",
    "fails",
    "failing",
    "failed",
    "unable",
    "cannot",
    "can't",
    "crash",
    "prompt",
    "password",
    "outlook",
    "teams",
    "windows",
    "laptop",
    "printer",
    "network",
    "wifi",
    "vpn",
    "application",
    "device",
    "service",
    "incident",
)
TROUBLESHOOTING_HINTS = (
    "tried",
    "restarted",
    "rebooted",
    "reinstalled",
    "cleared",
    "reset",
    "tested",
    "checked",
    "removed",
    "updated",
    "recreated",
    "disabled",
    "enabled",
    "deleted",
    "changed",
)
SOCIAL_HINTS = (
    "thanks",
    "thank you",
    "nice one",
    "well done",
    "great work",
    "good job",
    "awesome",
    "morning",
    "afternoon",
    "congrats",
)


class MessageKind(StrEnum):
    SOCIAL = "social"
    TECHNICAL_QUESTION = "technical_question"
    SUPPORT_SIGNAL = "support_signal"
    TROUBLESHOOTING = "troubleshooting"
    INCIDENT_REFERENCE = "incident_reference"
    POSSIBLE_RESOLUTION = "possible_resolution"
    ASSISTANT_SUPPRESS = "assistant_suppress"
    ASSISTANT_RESUME = "assistant_resume"
    OTHER = "other"


@dataclass(frozen=True)
class CollaborationDecision:
    kind: MessageKind
    invoke_agent: bool = False
    prompt_for_incident: bool = False
    prompt_for_capture: bool = False
    incident_number: str | None = None
    agent_query: str = ""
    assistant_suppressed: bool = False


@dataclass(frozen=True)
class ThreadState:
    channel_id: str
    thread_ts: str
    requester_id: str
    incident_number: str | None
    status: str
    resolver_id: str | None
    root_message: str
    participants: tuple[str, ...]
    assistant_suppressed: bool = False
    awaiting_clarification: bool = False


class FrontendThreadStore:
    """SQLite persistence for public case memory, private coaching and knowledge gaps."""

    def __init__(self, path: Path | None = None):
        self.path = path or settings.platform_db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS frontend_thread_state (
                    channel_id TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    requester_id TEXT NOT NULL,
                    incident_number TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    resolver_id TEXT,
                    root_message TEXT NOT NULL DEFAULT '',
                    participants_json TEXT NOT NULL DEFAULT '[]',
                    assistant_suppressed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (channel_id, thread_ts)
                );

                CREATE TABLE IF NOT EXISTS frontend_thread_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    message_ts TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message_kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    UNIQUE(channel_id, message_ts)
                );

                CREATE TABLE IF NOT EXISTS frontend_private_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message_ts TEXT NOT NULL,
                    message_kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    UNIQUE(channel_id, message_ts)
                );

                CREATE TABLE IF NOT EXISTS frontend_reusable_knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    incident_number TEXT NOT NULL,
                    requester_id TEXT NOT NULL,
                    resolver_id TEXT,
                    symptom TEXT NOT NULL,
                    resolution TEXT NOT NULL,
                    contributors_json TEXT NOT NULL,
                    source_events_json TEXT NOT NULL,
                    confirmed_by TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'trusted',
                    UNIQUE(channel_id, thread_ts)
                );

                CREATE TABLE IF NOT EXISTS frontend_knowledge_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_channel_id TEXT NOT NULL,
                    source_thread_ts TEXT NOT NULL,
                    incident_number TEXT NOT NULL,
                    symptom TEXT NOT NULL,
                    resolution TEXT NOT NULL,
                    contributors_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_channel_id, source_thread_ts)
                );
                """
            )
            # Safe migration for databases created before weekend-MVP suppression support.
            self._ensure_column(
                conn,
                "frontend_thread_state",
                "assistant_suppressed",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn,
                "frontend_thread_state",
                "awaiting_clarification",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(conn, "frontend_knowledge_tasks", "assignee_id", "TEXT")
            self._ensure_column(conn, "frontend_knowledge_tasks", "related_document_id", "TEXT")
            self._ensure_column(conn, "frontend_knowledge_tasks", "related_document_title", "TEXT")
            self._ensure_column(
                conn, "frontend_knowledge_tasks", "drafted_title", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                conn, "frontend_knowledge_tasks", "drafted_markdown", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(conn, "frontend_knowledge_tasks", "published_document_id", "TEXT")

    def ensure_thread(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        requester_id: str,
        root_message: str,
    ) -> ThreadState:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO frontend_thread_state
                    (channel_id, thread_ts, requester_id, root_message, participants_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (channel_id, thread_ts, requester_id, root_message, json.dumps([requester_id])),
            )
        return self.get_thread(channel_id, thread_ts)

    def get_thread(self, channel_id: str, thread_ts: str) -> ThreadState:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM frontend_thread_state WHERE channel_id=? AND thread_ts=?",
                (channel_id, thread_ts),
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown frontend-support thread {channel_id}/{thread_ts}")
        return ThreadState(
            channel_id=row["channel_id"],
            thread_ts=row["thread_ts"],
            requester_id=row["requester_id"],
            incident_number=row["incident_number"],
            status=row["status"],
            resolver_id=row["resolver_id"],
            root_message=row["root_message"],
            participants=tuple(json.loads(row["participants_json"] or "[]")),
            assistant_suppressed=bool(row["assistant_suppressed"]),
            awaiting_clarification=bool(row["awaiting_clarification"]),
        )

    def add_event(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
        user_id: str,
        kind: MessageKind,
        text: str,
    ) -> None:
        state = self.get_thread(channel_id, thread_ts)
        participants = list(state.participants)
        if user_id not in participants:
            participants.append(user_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO frontend_thread_events
                    (channel_id, thread_ts, message_ts, user_id, message_kind, text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (channel_id, thread_ts, message_ts, user_id, kind.value, text),
            )
            conn.execute(
                """
                UPDATE frontend_thread_state SET participants_json=?
                WHERE channel_id=? AND thread_ts=?
                """,
                (json.dumps(participants), channel_id, thread_ts),
            )

    def bind_incident(self, channel_id: str, thread_ts: str, incident_number: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE frontend_thread_state SET incident_number=?
                WHERE channel_id=? AND thread_ts=?
                """,
                (incident_number.upper(), channel_id, thread_ts),
            )

    def set_suppressed(self, channel_id: str, thread_ts: str, suppressed: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE frontend_thread_state SET assistant_suppressed=?
                WHERE channel_id=? AND thread_ts=?
                """,
                (1 if suppressed else 0, channel_id, thread_ts),
            )

    def set_awaiting_clarification(self, channel_id: str, thread_ts: str, awaiting: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE frontend_thread_state SET awaiting_clarification=?
                WHERE channel_id=? AND thread_ts=?
                """,
                (1 if awaiting else 0, channel_id, thread_ts),
            )

    def mark_possible_resolution(self, channel_id: str, thread_ts: str, resolver_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE frontend_thread_state SET status='possible_resolution', resolver_id=?
                WHERE channel_id=? AND thread_ts=?
                """,
                (resolver_id, channel_id, thread_ts),
            )

    def recent_events(self, channel_id: str, thread_ts: str, limit: int = 12) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_ts, user_id, message_kind, text
                FROM frontend_thread_events
                WHERE channel_id=? AND thread_ts=?
                ORDER BY id DESC LIMIT ?
                """,
                (channel_id, thread_ts, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def add_private_event(
        self,
        *,
        channel_id: str,
        user_id: str,
        message_ts: str,
        kind: MessageKind,
        text: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO frontend_private_events
                    (channel_id, user_id, message_ts, message_kind, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (channel_id, user_id, message_ts, kind.value, text),
            )

    def recent_private_events(
        self, channel_id: str, user_id: str, limit: int = 16
    ) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_ts, message_kind, text
                FROM frontend_private_events
                WHERE channel_id=? AND user_id=?
                ORDER BY id DESC LIMIT ?
                """,
                (channel_id, user_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    @staticmethod
    def _resolution_summary(events: list[dict]) -> str:
        resolution_events = [
            e for e in events if e["message_kind"] == MessageKind.POSSIBLE_RESOLUTION.value
        ]
        troubleshooting = [
            e for e in events if e["message_kind"] == MessageKind.TROUBLESHOOTING.value
        ]
        parts: list[str] = []
        if troubleshooting:
            parts.append(troubleshooting[-1]["text"])
        if resolution_events:
            confirmation = resolution_events[-1]["text"]
            if confirmation not in parts:
                parts.append(confirmation)
        if not parts and events:
            parts.append(events[-1]["text"])
        return " | ".join(parts) or "Confirmed resolution"

    def confirm_knowledge(self, channel_id: str, thread_ts: str, actor_id: str) -> str:
        state = self.get_thread(channel_id, thread_ts)
        if actor_id not in {state.requester_id, state.resolver_id}:
            return "Only the original requester or the identified resolver can confirm this knowledge capture."
        if not state.incident_number:
            return "Please add the ServiceNow incident number to this thread before capturing it as reusable knowledge."

        events = self.recent_events(channel_id, thread_ts, limit=50)
        resolution = self._resolution_summary(events)
        contributors = sorted({e["user_id"] for e in events})

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO frontend_reusable_knowledge
                    (channel_id, thread_ts, incident_number, requester_id, resolver_id,
                     symptom, resolution, contributors_json, source_events_json,
                     confirmed_by, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'trusted')
                """,
                (
                    channel_id,
                    thread_ts,
                    state.incident_number,
                    state.requester_id,
                    state.resolver_id,
                    state.root_message,
                    resolution,
                    json.dumps(contributors),
                    json.dumps(events),
                    actor_id,
                ),
            )
            conn.execute(
                """
                UPDATE frontend_thread_state SET status='resolved'
                WHERE channel_id=? AND thread_ts=?
                """,
                (channel_id, thread_ts),
            )
        return f"Captured this resolution as trusted reusable knowledge for {state.incident_number}."

    def create_knowledge_task(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        created_by: str,
        reason: str,
        related_document_id: str | None = None,
        related_document_title: str | None = None,
        drafted_title: str = "",
        drafted_markdown: str = "",
    ) -> dict:
        state = self.get_thread(channel_id, thread_ts)
        if not state.incident_number:
            raise ValueError("A knowledge task requires a bound incident number")
        events = self.recent_events(channel_id, thread_ts, limit=50)
        resolution = self._resolution_summary(events)
        contributors = sorted({e["user_id"] for e in events})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO frontend_knowledge_tasks
                    (source_channel_id, source_thread_ts, incident_number, symptom,
                     resolution, contributors_json, reason, created_by,
                     related_document_id, related_document_title, drafted_title, drafted_markdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    thread_ts,
                    state.incident_number,
                    state.root_message,
                    resolution,
                    json.dumps(contributors),
                    reason,
                    created_by,
                    related_document_id,
                    related_document_title,
                    drafted_title,
                    drafted_markdown,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM frontend_knowledge_tasks
                WHERE source_channel_id=? AND source_thread_ts=?
                """,
                (channel_id, thread_ts),
            ).fetchone()
        if not row:
            raise RuntimeError("Knowledge task was not created")
        return self._task_row_to_dict(row)

    @staticmethod
    def _task_row_to_dict(row: sqlite3.Row) -> dict:
        task = dict(row)
        task["task_id"] = f"KG-{int(task['id']):05d}"
        task["contributors"] = json.loads(task.pop("contributors_json") or "[]")
        return task

    def get_knowledge_task(self, task_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM frontend_knowledge_tasks WHERE id=?", (task_id,)
            ).fetchone()
        return self._task_row_to_dict(row) if row else None

    def assign_knowledge_task(self, task_id: int, assignee_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE frontend_knowledge_tasks SET assignee_id=?, status='assigned' WHERE id=?",
                (assignee_id, task_id),
            )

    def mark_knowledge_task_published(self, task_id: int, *, published_document_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE frontend_knowledge_tasks SET status='published', published_document_id=? WHERE id=?",
                (published_document_id, task_id),
            )

    def dismiss_knowledge_task(self, task_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE frontend_knowledge_tasks SET status='dismissed' WHERE id=?", (task_id,)
            )


class FrontendCollaborationService:
    def __init__(
        self,
        store: FrontendThreadStore | None = None,
        graph: SupportKnowledgeGraph | None = None,
    ):
        self.store = store or FrontendThreadStore()
        self.graph = graph or SupportKnowledgeGraph()

    @staticmethod
    def classify(text: str) -> MessageKind:
        clean = " ".join((text or "").split())
        lowered = clean.lower()
        if not clean:
            return MessageKind.OTHER
        if any(pattern.search(clean) for pattern in RESUME_PATTERNS):
            return MessageKind.ASSISTANT_RESUME
        if any(pattern.search(clean) for pattern in SUPPRESS_PATTERNS):
            return MessageKind.ASSISTANT_SUPPRESS
        if any(pattern.search(clean) for pattern in RESOLUTION_PATTERNS):
            return MessageKind.POSSIBLE_RESOLUTION
        if INCIDENT_RE.search(clean) and len(clean.split()) <= 8:
            return MessageKind.INCIDENT_REFERENCE
        if any(hint in lowered for hint in TROUBLESHOOTING_HINTS):
            return MessageKind.TROUBLESHOOTING
        if "?" in clean and any(hint in lowered for hint in TECHNICAL_HINTS):
            return MessageKind.TECHNICAL_QUESTION
        if any(hint in lowered for hint in TECHNICAL_HINTS):
            return MessageKind.SUPPORT_SIGNAL
        if any(hint in lowered for hint in SOCIAL_HINTS):
            return MessageKind.SOCIAL
        return MessageKind.OTHER

    def observe(
        self,
        *,
        channel_id: str,
        message_ts: str,
        thread_ts: str | None,
        user_id: str,
        text: str,
    ) -> CollaborationDecision:
        root_ts = thread_ts or message_ts
        is_root = thread_ts is None
        kind = self.classify(text)

        if is_root:
            self.store.ensure_thread(
                channel_id=channel_id,
                thread_ts=root_ts,
                requester_id=user_id,
                root_message=text,
            )
        else:
            try:
                self.store.get_thread(channel_id, root_ts)
            except KeyError:
                self.store.ensure_thread(
                    channel_id=channel_id,
                    thread_ts=root_ts,
                    requester_id=user_id,
                    root_message="",
                )

        self.store.add_event(
            channel_id=channel_id,
            thread_ts=root_ts,
            message_ts=message_ts,
            user_id=user_id,
            kind=kind,
            text=text,
        )
        state = self.store.get_thread(channel_id, root_ts)

        # A reply to the assistant's own clarifying question should route back to
        # it even when the reply text alone doesn't look technical (e.g. "Dell
        # Latitude 5420" in answer to "what's the device model?"). The flag is
        # single-use: whatever arrives next consumes it, whether or not it was
        # actually the requested detail.
        was_awaiting_clarification = state.awaiting_clarification
        if was_awaiting_clarification:
            self.store.set_awaiting_clarification(channel_id, root_ts, False)

        incident_match = INCIDENT_RE.search(text or "")
        incident_number = incident_match.group(0).upper() if incident_match else None
        if incident_number and user_id == state.requester_id:
            self.store.bind_incident(channel_id, root_ts, incident_number)
            state = self.store.get_thread(channel_id, root_ts)

        if kind == MessageKind.ASSISTANT_SUPPRESS:
            self.store.set_suppressed(channel_id, root_ts, True)
            return CollaborationDecision(
                kind=kind,
                incident_number=state.incident_number,
                assistant_suppressed=True,
            )
        if kind == MessageKind.ASSISTANT_RESUME:
            self.store.set_suppressed(channel_id, root_ts, False)
            return CollaborationDecision(
                kind=kind,
                incident_number=state.incident_number,
                assistant_suppressed=False,
            )

        if kind == MessageKind.POSSIBLE_RESOLUTION:
            self.store.mark_possible_resolution(channel_id, root_ts, user_id)
            return CollaborationDecision(
                kind=kind,
                prompt_for_capture=True,
                incident_number=state.incident_number,
                assistant_suppressed=state.assistant_suppressed,
            )

        meaningful = {
            MessageKind.TECHNICAL_QUESTION,
            MessageKind.SUPPORT_SIGNAL,
            MessageKind.TROUBLESHOOTING,
        }
        invoke_agent = (
            kind in meaningful or was_awaiting_clarification
        ) and not state.assistant_suppressed
        prompt_for_incident = bool(
            is_root
            and kind in {MessageKind.TECHNICAL_QUESTION, MessageKind.SUPPORT_SIGNAL}
            and not state.incident_number
        )

        return CollaborationDecision(
            kind=kind,
            invoke_agent=invoke_agent,
            prompt_for_incident=prompt_for_incident,
            incident_number=state.incident_number,
            agent_query=self.build_agent_query(channel_id, root_ts) if invoke_agent else "",
            assistant_suppressed=state.assistant_suppressed,
        )

    def observe_private(
        self,
        *,
        channel_id: str,
        message_ts: str,
        user_id: str,
        text: str,
    ) -> str:
        kind = self.classify(text)
        self.store.add_private_event(
            channel_id=channel_id,
            user_id=user_id,
            message_ts=message_ts,
            kind=kind,
            text=text,
        )
        return self.build_private_query(channel_id, user_id)

    def build_agent_query(self, channel_id: str, thread_ts: str) -> str:
        state = self.store.get_thread(channel_id, thread_ts)
        events = self.store.recent_events(channel_id, thread_ts)
        lines = [
            "Current collaborative Slack thread:",
            f"Incident: {state.incident_number or 'not yet supplied'}",
            f"Requester: {state.requester_id}",
        ]
        for event in events:
            lines.append(f"- {event['user_id']} [{event['message_kind']}]: {event['text']}")
        lines.extend(
            [
                "Treat every human contribution as current case evidence.",
                "Do not repeat a troubleshooting step already reported as unsuccessful.",
                "Respond only if you can add useful troubleshooting knowledge, repeat-incident evidence, "
                "a warning about a known failed path, or a concise collaborative next step.",
            ]
        )
        return "\n".join(lines)

    def build_private_query(self, channel_id: str, user_id: str) -> str:
        events = self.store.recent_private_events(channel_id, user_id)
        lines = [
            "PRIVATE COACHING SESSION.",
            "This is a one-on-one troubleshooting space for the technician.",
            "Never expose, quote, attribute, or imply private conversation content in a public channel.",
            "If something should be shared publicly, offer a sanitized technical summary first.",
            "Recent private conversation:",
        ]
        for event in events:
            lines.append(f"- technician [{event['message_kind']}]: {event['text']}")
        lines.append(
            "Coach without judgement. Explain unfamiliar concepts when needed, use internal incident history "
            "and governed knowledge, and help the technician choose the next safest useful action."
        )
        return "\n".join(lines)

    def create_knowledge_gap_task(
        self,
        channel_id: str,
        thread_ts: str,
        actor_id: str,
        reason: str,
        *,
        related_document_id: str | None = None,
        related_document_title: str | None = None,
        drafted_title: str = "",
        drafted_markdown: str = "",
    ) -> dict:
        return self.store.create_knowledge_task(
            channel_id=channel_id,
            thread_ts=thread_ts,
            created_by=actor_id,
            reason=reason,
            related_document_id=related_document_id,
            related_document_title=related_document_title,
            drafted_title=drafted_title,
            drafted_markdown=drafted_markdown,
        )

    def confirm_knowledge(self, channel_id: str, thread_ts: str, actor_id: str) -> str:
        result = self.store.confirm_knowledge(channel_id, thread_ts, actor_id)
        if not result.startswith("Captured this resolution"):
            return result

        state = self.store.get_thread(channel_id, thread_ts)
        events = self.store.recent_events(channel_id, thread_ts, limit=50)
        resolution_text = self.store._resolution_summary(events)
        incident = self.graph.add_incident(
            state.incident_number or "unknown",
            short_description=state.root_message,
            state="Resolved",
        )
        if state.root_message:
            self.graph.add_symptom(incident, state.root_message, confidence=1.0)
        self.graph.add_resolution(
            incident,
            resolution_text,
            resolver=state.resolver_id,
            confidence=1.0,
        )
        thread = self.graph.link_thread(incident, channel_id, thread_ts)
        knowledge = GraphEntity(
            SupportEntityType.KNOWLEDGE_ITEM,
            f"slack:{channel_id}:{thread_ts}",
            f"Reusable resolution for {state.incident_number}",
            {"status": "trusted", "source": "slack_thread"},
        )
        self.graph.relate(thread, knowledge, SupportRelation.PROMOTED_TO)
        self.graph.save()
        return result

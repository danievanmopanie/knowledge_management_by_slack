"""Small-model extraction of structured support knowledge from incident free text."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from src.core.config import settings
from src.knowledge.incident_dedupe import content_hash
from src.knowledge.support_graph import (
    GraphEntity,
    SupportEntityType,
    SupportKnowledgeGraph,
    SupportRelation,
)
from src.llm.client import get_support_extraction_llm
from src.reporting.incidents import Incident

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You extract reusable IT support knowledge from ServiceNow incident text.
Return one JSON object only. Do not include markdown.

Rules:
- Use only facts present in the supplied incident. Never invent missing details.
- Keep symptoms and resolutions concise but specific enough for future retrieval.
- Separate troubleshooting actions in the order they appear when possible.
- Outcome must be one of successful, failed, unknown.
- Root cause must be empty when it is not explicitly supported.
- A person/contributor must be empty unless the text or structured incident fields identify them.
- Confidence is 0.0 to 1.0 and reflects evidence quality, not how plausible the fix sounds.
- Technologies and environment are short canonical labels, not sentences.
"""


class ExtractedAction(BaseModel):
    action: str = ""
    outcome: Literal["successful", "failed", "unknown"] = "unknown"
    contributor: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class SupportExtraction(BaseModel):
    symptom: str = ""
    environment: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    actions: list[ExtractedAction] = Field(default_factory=list)
    root_cause: str = ""
    resolution: str = ""
    resolver: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class SupportExtractionStore:
    """Content-hash cache so unchanged daily incident snapshots are not re-extracted."""

    def __init__(self, path: Path | None = None):
        self.path = path or settings.platform_db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS support_incident_extractions (
                    incident_number TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    extraction_json TEXT NOT NULL,
                    PRIMARY KEY (incident_number, content_hash, model)
                )
                """
            )

    def get(self, incident: Incident) -> SupportExtraction | None:
        digest = content_hash(incident)
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                """
                SELECT extraction_json FROM support_incident_extractions
                WHERE incident_number=? AND content_hash=? AND model=?
                """,
                (incident.number, digest, settings.support_extraction_model),
            ).fetchone()
        if not row:
            return None
        try:
            return SupportExtraction.model_validate_json(row[0])
        except ValidationError:
            return None

    def put(self, incident: Incident, extraction: SupportExtraction) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO support_incident_extractions
                    (incident_number, content_hash, model, extraction_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    incident.number,
                    content_hash(incident),
                    settings.support_extraction_model,
                    extraction.model_dump_json(),
                ),
            )


def incident_extraction_text(incident: Incident) -> str:
    """Build evidence-preserving text for extraction without huge journal payloads."""
    parts = [
        f"Incident: {incident.number}",
        f"State: {incident.state}",
        f"Category: {incident.category}",
        f"Subcategory: {incident.subcategory}",
        f"Location: {incident.location}",
        f"Assigned to: {incident.assigned_to}",
        f"Short description: {incident.short_description}",
        f"Description: {incident.description}",
        f"Work notes: {incident.work_notes}",
        f"Comments: {incident.comments}",
        f"Resolution notes: {incident.resolution_notes}",
    ]
    text = "\n".join(part for part in parts if not part.endswith(": "))
    return text[: settings.support_extraction_max_chars]


def _parse_json_object(value: str) -> dict:
    clean = (value or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(clean[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Extraction model did not return a JSON object")


class SupportKnowledgeExtractor:
    """Extract structured support facts with a small local LLM, then seed the graph."""

    def __init__(
        self,
        *,
        llm=None,
        graph: SupportKnowledgeGraph | None = None,
        cache: SupportExtractionStore | None = None,
    ):
        self.llm = llm or get_support_extraction_llm()
        self.graph = graph or SupportKnowledgeGraph()
        self.cache = cache or SupportExtractionStore()

    async def extract(self, incident: Incident, *, force: bool = False) -> SupportExtraction:
        if not force:
            cached = self.cache.get(incident)
            if cached is not None:
                return cached

        schema_example = SupportExtraction(
            symptom="Concise user-visible symptom",
            environment=["Windows 11"],
            technologies=["Microsoft Outlook"],
            actions=[
                ExtractedAction(
                    action="Recreated Outlook profile",
                    outcome="failed",
                    contributor="Technician Name",
                    confidence=0.9,
                )
            ],
            root_cause="Corrupted authentication token",
            resolution="Cleared authentication token cache",
            resolver="Technician Name",
            confidence=0.9,
        ).model_dump()
        prompt = (
            "Extract this incident into the JSON shape shown below. Empty values are preferred over guesses.\n\n"
            f"JSON shape example:\n{json.dumps(schema_example, ensure_ascii=False)}\n\n"
            f"INCIDENT EVIDENCE:\n{incident_extraction_text(incident)}"
        )
        response = await self.llm.ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
        )
        content = response.content if hasattr(response, "content") else str(response)
        extraction = SupportExtraction.model_validate(_parse_json_object(str(content)))
        self.cache.put(incident, extraction)
        return extraction

    def apply(self, incident: Incident, extraction: SupportExtraction) -> None:
        incident_entity = self.graph.add_incident(
            incident.number,
            short_description=incident.short_description,
            state=incident.state,
            assigned_to=incident.assigned_to,
            caller=incident.caller,
        )

        if extraction.symptom:
            self.graph.add_symptom(
                incident_entity,
                extraction.symptom,
                confidence=extraction.confidence,
            )

        for technology in extraction.technologies:
            if not technology.strip():
                continue
            entity = GraphEntity(
                SupportEntityType.TECHNOLOGY,
                technology,
                technology.strip(),
            )
            self.graph.relate(incident_entity, entity, SupportRelation.AFFECTS)

        for environment in extraction.environment:
            if not environment.strip():
                continue
            entity = GraphEntity(
                SupportEntityType.ENVIRONMENT,
                environment,
                environment.strip(),
            )
            self.graph.relate(incident_entity, entity, SupportRelation.OCCURRED_IN)

        for action in extraction.actions:
            if action.action.strip():
                self.graph.add_action(
                    incident_entity,
                    action.action,
                    outcome=action.outcome,
                    contributor=action.contributor or None,
                )

        resolution = extraction.resolution.strip() or incident.resolution_notes.strip()
        if resolution:
            resolver = extraction.resolver.strip() or incident.assigned_to.strip() or None
            self.graph.add_resolution(
                incident_entity,
                resolution,
                resolver=resolver,
                root_cause=extraction.root_cause,
                confidence=extraction.confidence,
            )
        self.graph.save()

    async def extract_and_apply(
        self, incident: Incident, *, force: bool = False
    ) -> SupportExtraction:
        extraction = await self.extract(incident, force=force)
        self.apply(incident, extraction)
        return extraction

    async def extract_many(
        self,
        incidents: list[Incident],
        *,
        force: bool = False,
        concurrency: int | None = None,
    ) -> dict[str, int]:
        worker_count = concurrency or settings.support_extraction_concurrency
        semaphore = asyncio.Semaphore(worker_count)
        total = len(incidents)
        stats = {"processed": 0, "failed": 0, "cached": 0, "extracted": 0}

        logger.info(
            "Starting support extraction: total=%d model='%s' concurrency=%d force=%s",
            total,
            settings.support_extraction_model,
            worker_count,
            force,
        )

        async def process(incident: Incident) -> None:
            async with semaphore:
                cached = None if force else self.cache.get(incident)
                mode = "cached" if cached is not None else "model"
                evidence_chars = len(incident_extraction_text(incident))
                logger.info(
                    "Starting %s [%s]: mode=%s evidence_chars=%d",
                    incident.number,
                    incident.state or "unknown",
                    mode,
                    evidence_chars,
                )
                try:
                    if cached is not None:
                        self.apply(incident, cached)
                        stats["cached"] += 1
                    else:
                        await self.extract_and_apply(incident, force=force)
                        stats["extracted"] += 1
                    stats["processed"] += 1
                    completed = stats["processed"] + stats["failed"]
                    logger.info(
                        "Completed %s [%d/%d]: %s (processed=%d cached=%d extracted=%d failed=%d)",
                        incident.number,
                        completed,
                        total,
                        mode,
                        stats["processed"],
                        stats["cached"],
                        stats["extracted"],
                        stats["failed"],
                    )
                except Exception:
                    stats["failed"] += 1
                    completed = stats["processed"] + stats["failed"]
                    logger.exception(
                        "Failed %s [%d/%d] (processed=%d failed=%d)",
                        incident.number,
                        completed,
                        total,
                        stats["processed"],
                        stats["failed"],
                    )

        await asyncio.gather(*(process(incident) for incident in incidents))
        return stats

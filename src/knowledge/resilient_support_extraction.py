"""Resilient support extraction that preserves malformed output as untrusted evidence."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.knowledge.support_extraction import (
    SYSTEM_PROMPT,
    ExtractedAction,
    SupportExtraction,
    SupportKnowledgeExtractor as BaseSupportKnowledgeExtractor,
    _parse_json_object,
    incident_extraction_text,
)


def _sanitise_payload(payload: dict) -> dict:
    """Normalise model-shape defects without upgrading their trustworthiness."""
    value = dict(payload or {})
    if value.get("confidence") is None:
        value["confidence"] = 0.0
    actions = []
    for raw in value.get("actions") or []:
        if not isinstance(raw, dict):
            continue
        action = dict(raw)
        if action.get("confidence") is None:
            action["confidence"] = 0.0
        actions.append(action)
    value["actions"] = actions
    return value


class SupportKnowledgeExtractor(BaseSupportKnowledgeExtractor):
    """Same ontology as the base extractor, with safe null-confidence handling."""

    async def extract(self, incident, *, force: bool = False) -> SupportExtraction:
        if not force:
            cached = self.cache.get(incident)
            if cached is not None:
                return cached

        schema_example = SupportExtraction(
            issue_pattern="Microsoft Outlook repeated credential prompt",
            symptom="Outlook prompts for the user's password every time it opens after a password change",
            environment=["Windows 11"],
            technologies=["Microsoft Outlook", "Microsoft 365"],
            actions=[
                ExtractedAction(
                    action="Technician recreated the Outlook profile but the prompt returned",
                    canonical_action="Recreate Outlook profile",
                    outcome="failed",
                    contributor="Technician Name",
                    confidence=0.9,
                ),
                ExtractedAction(
                    action="Removed stale Office credentials and signed in again",
                    canonical_action="Clear cached Office credentials",
                    outcome="successful",
                    contributor="Technician Name",
                    confidence=0.95,
                ),
            ],
            root_cause="Stale cached Office credential remained after the password changed",
            root_cause_pattern="Stale cached authentication credential",
            resolution="Removed the stale cached Office credential and Outlook accepted the new password",
            resolution_pattern="Clear cached Office credentials",
            resolver="Technician Name",
            confidence=0.9,
        ).model_dump()
        prompt = (
            "Extract this incident into the JSON shape shown below. Preserve evidence detail while also "
            "producing canonical pattern labels for cross-incident aggregation. Empty values are preferred "
            "over guesses. A passive recovery (for example self-recovered/no intervention) is an outcome, "
            "not a reusable resolution procedure.\n\n"
            f"JSON shape example:\n{json.dumps(schema_example, ensure_ascii=False)}\n\n"
            f"INCIDENT EVIDENCE:\n{incident_extraction_text(incident)}"
        )
        response = await self.llm.ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
        )
        content = response.content if hasattr(response, "content") else str(response)
        payload = _sanitise_payload(_parse_json_object(str(content)))
        extraction = SupportExtraction.model_validate(payload)
        self.cache.put(incident, extraction)
        return extraction

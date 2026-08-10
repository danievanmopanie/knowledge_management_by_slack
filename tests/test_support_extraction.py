"""Tests for small-model support knowledge extraction and caching."""

import asyncio
import json

from src.knowledge.graphstore import GraphStore
from src.knowledge.support_extraction import (
    SupportExtractionStore,
    SupportKnowledgeExtractor,
    incident_extraction_text,
)
from src.knowledge.support_graph import SupportKnowledgeGraph
from src.reporting.incidents import Incident


class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeExtractionLLM:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return FakeResponse(
            json.dumps(
                {
                    "symptom": "Outlook repeatedly prompts for credentials",
                    "environment": ["Windows 11"],
                    "technologies": ["Microsoft Outlook"],
                    "actions": [
                        {
                            "action": "Recreated Outlook profile",
                            "outcome": "failed",
                            "contributor": "Jane Tech",
                            "confidence": 0.95,
                        },
                        {
                            "action": "Cleared authentication token cache",
                            "outcome": "successful",
                            "contributor": "Jane Tech",
                            "confidence": 0.95,
                        },
                    ],
                    "root_cause": "Corrupted authentication token cache",
                    "resolution": "Cleared authentication token cache",
                    "resolver": "Jane Tech",
                    "confidence": 0.94,
                }
            )
        )


def _incident():
    return Incident(
        number="INC0012345",
        short_description="Outlook password prompt",
        description="User is repeatedly prompted for credentials.",
        work_notes="Jane Tech recreated the Outlook profile with no change, then cleared the token cache.",
        resolution_notes="Cleared token cache and Outlook worked normally.",
        state="Resolved",
        assigned_to="Jane Tech",
        category="Software",
        subcategory="Microsoft 365",
    )


def test_extraction_text_preserves_free_text_and_provenance():
    text = incident_extraction_text(_incident())
    assert "INC0012345" in text
    assert "Work notes:" in text
    assert "Resolution notes:" in text
    assert "Assigned to: Jane Tech" in text


def test_extractor_caches_unchanged_incident(tmp_path):
    llm = FakeExtractionLLM()
    extractor = SupportKnowledgeExtractor(
        llm=llm,
        graph=SupportKnowledgeGraph(GraphStore(path=tmp_path / "graph")),
        cache=SupportExtractionStore(tmp_path / "platform.db"),
    )

    first = asyncio.run(extractor.extract(_incident()))
    second = asyncio.run(extractor.extract(_incident()))

    assert first.resolution == "Cleared authentication token cache"
    assert second.resolution == first.resolution
    assert llm.calls == 1


def test_extracted_facts_are_applied_to_support_graph(tmp_path):
    llm = FakeExtractionLLM()
    graph = SupportKnowledgeGraph(GraphStore(path=tmp_path / "graph"))
    extractor = SupportKnowledgeExtractor(
        llm=llm,
        graph=graph,
        cache=SupportExtractionStore(tmp_path / "platform.db"),
    )

    asyncio.run(extractor.extract_and_apply(_incident()))
    related = graph.related("incident:inc0012345", depth=1)
    relations = {item["relation"] for item in related}

    assert "has_symptom" in relations
    assert "affects" in relations
    assert "occurred_in" in relations
    assert "tried" in relations
    assert "failed_action" in relations
    assert "successful_fix" in relations

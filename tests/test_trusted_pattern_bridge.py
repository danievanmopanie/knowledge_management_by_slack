from dataclasses import dataclass

from src.knowledge.organisational_knowledge import EnrichedIncidentKnowledge
from src.knowledge.support_extraction import SupportExtraction
from src.knowledge.trusted_pattern_retriever import TrustedPatternKnowledgeRetriever


@dataclass
class _Doc:
    metadata: dict


class _VectorStore:
    def __init__(self, docs):
        self.docs = docs

    def similarity_search(self, *_args, **_kwargs):
        return self.docs


class _Index:
    def __init__(self, docs):
        self.vector_store = _VectorStore(docs)


class _Store:
    def __init__(self, items, pattern):
        self.items = {item.incident_number: item for item in items}
        self.pattern = pattern

    def get(self, number):
        return self.items.get(number)

    def get_pattern(self, key):
        if key == self.pattern["pattern_key"]:
            return self.pattern
        return None


def _item(number, *, pattern_key, pattern_label, symptom, resolution):
    extraction = SupportExtraction(
        issue_pattern=pattern_label,
        symptom=symptom,
        resolution=resolution,
        resolution_pattern=resolution,
        confidence=0.95,
    )
    return EnrichedIncidentKnowledge(
        incident_number=number,
        row_hash=f"hash-{number}",
        model="qwen3:30b-a3b|v2",
        source_file="test.csv",
        pattern_key=pattern_key,
        pattern_label=pattern_label,
        symptom=symptom,
        resolution=resolution,
        resolution_pattern=resolution,
        root_cause="",
        root_cause_pattern="",
        technologies=("Network switch",),
        environments=(),
        location="144 Oxford Rd JHB",
        assignment_group="Network Support",
        confidence=0.95,
        extraction=extraction,
        actions=(),
    )


def _retriever():
    switch_resolutions = {
        "INC0022194": "Restore power via electrician",
        "INC0022195": "Restore power via electrician",
        "INC0022253": "Restore power to network switch",
        "INC0022228": "Connect switch sole",
        "INC0022229": "Reconnect switch",
    }
    switch_items = [
        _item(
            number,
            pattern_key="network-switch-offline",
            pattern_label="Network switch offline",
            symptom="Network switch offline",
            resolution=resolution,
        )
        for number, resolution in switch_resolutions.items()
    ]
    node = _item(
        "INC0022454",
        pattern_key="network-node-offline",
        pattern_label="Network node offline",
        symptom="Network node offline",
        resolution="Network node self-recovered",
    )
    account = _item(
        "INC0022110",
        pattern_key="application-account-disabled",
        pattern_label="Application account disabled",
        symptom="Application account disabled",
        resolution="Enable application account",
    )

    docs = [
        _Doc({"number": "INC0022194", "score": 0.67}),
        _Doc({"number": "INC0022253", "score": 0.66}),
        _Doc({"number": "INC0022228", "score": 0.65}),
        _Doc({"number": "INC0022195", "score": 0.64}),
        _Doc({"number": "INC0022229", "score": 0.63}),
        _Doc({"number": "INC0022454", "score": 0.60}),
        _Doc({"number": "INC0022110", "score": 0.58}),
    ]
    pattern = {
        "pattern_key": "network-switch-offline",
        "label": "Network switch offline",
        "incident_count": 5,
        "resolved_count": 5,
        "avg_confidence": 0.95,
        "resolution_counts": [
            {
                "label": "Restore electrical power to network equipment",
                "count": 3,
                "incidents": ["INC0022194", "INC0022195", "INC0022253"],
            },
            {
                "label": "Reconnect network switch",
                "count": 2,
                "incidents": ["INC0022228", "INC0022229"],
            },
        ],
        "successful_action_counts": [
            {
                "label": "Restore electrical power to network equipment",
                "count": 3,
                "incidents": ["INC0022194", "INC0022195", "INC0022253"],
            },
            {
                "label": "Reconnect network switch",
                "count": 2,
                "incidents": ["INC0022228", "INC0022229"],
            },
        ],
        "failed_action_counts": [],
        "root_cause_counts": [
            {
                "label": "Power transformer issue",
                "count": 1,
                "incidents": ["INC0022194"],
            },
            {
                "label": "Incorrect device connection to power source",
                "count": 1,
                "incidents": ["INC0022228"],
            },
        ],
        "incident_numbers": list(switch_resolutions),
    }
    return TrustedPatternKnowledgeRetriever(
        store=_Store([*switch_items, node, account], pattern),
        index=_Index(docs),
    )


def test_bridge_uses_semantics_only_to_select_authoritative_pattern():
    text = _retriever().collective_context(
        "Network switch is offline and users cannot connect"
    )

    assert "Canonical issue pattern: Network switch offline" in text
    assert "Trusted supporting incidents: 5" in text
    assert "Restore electrical power to network equipment: 3 incident(s)" in text
    assert "Reconnect network switch: 2 incident(s)" in text
    assert "Evidence strength: strong" in text
    assert "Connect switch sole" not in text
    assert "Restore power via electrician" not in text
    assert "Application account disabled" not in text
    assert "144 Oxford Rd JHB" not in text


def test_deterministic_response_preserves_counts_without_inventing_steps():
    text = _retriever().deterministic_response(
        "Network switch is offline and users cannot connect"
    )

    assert "5 trusted incidents" in text
    assert "3 of 5 trusted incidents" in text
    assert "2 of 5 trusted incidents" in text
    assert "Restore electrical power to network equipment" in text
    assert "Reconnect network switch" in text
    assert "does not support one universal root cause" in text
    assert "No repeated failed troubleshooting path" in text
    assert "60%" not in text
    assert "uplink" not in text.lower()
    assert "surge protector" not in text.lower()
    assert "misconfigured" not in text.lower()

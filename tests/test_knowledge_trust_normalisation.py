import json

from src.knowledge.knowledge_trust import (
    canonical_resolution_family,
    is_reusable_resolution,
    rebuild_trusted_patterns,
    safe_confidence,
)
from src.knowledge.organisational_knowledge import OrganisationalKnowledgeStore
from src.knowledge.resilient_support_extraction import _sanitise_payload


MODEL_KEY = "qwen3:30b-a3b|v2"


def _insert_knowledge(
    store,
    *,
    number,
    pattern_key,
    label,
    confidence,
    resolution="",
    action="",
    action_outcome="unknown",
    action_confidence=0.9,
):
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO support_incident_knowledge(
                incident_number,row_hash,model,source_file,pattern_key,pattern_label,
                symptom,resolution,resolution_pattern,root_cause,root_cause_pattern,
                technologies_json,environments_json,location,assignment_group,
                confidence,extraction_json,enriched_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                number,
                f"hash-{number}",
                MODEL_KEY,
                "test.csv",
                pattern_key,
                label,
                label,
                resolution,
                resolution,
                "",
                "",
                json.dumps(["Network switch"]),
                "[]",
                "Test Site",
                "Network Support",
                confidence,
                "{}",
                "2026-08-16T00:00:00+00:00",
            ),
        )
        if action:
            conn.execute(
                """
                INSERT INTO support_knowledge_actions(
                    incident_number,ordinal,action,canonical_action,outcome,contributor,confidence
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (number, 0, action, action, action_outcome, "", action_confidence),
            )


def test_confidence_null_is_preserved_as_untrusted():
    payload = _sanitise_payload(
        {
            "issue_pattern": "Network switch offline",
            "confidence": None,
            "actions": [{"action": "Ping switch", "confidence": None}],
        }
    )
    assert payload["confidence"] == 0.0
    assert payload["actions"][0]["confidence"] == 0.0
    assert safe_confidence(None) == 0.0


def test_obvious_power_variants_share_one_family():
    assert canonical_resolution_family("Restore power via electrician") == (
        "Restore electrical power to network equipment"
    )
    assert canonical_resolution_family("Restore power to network switch") == (
        "Restore electrical power to network equipment"
    )
    assert not is_reusable_resolution("Network node self-recovered")


def test_rollups_exclude_low_confidence_and_passive_recovery(tmp_path):
    store = OrganisationalKnowledgeStore(tmp_path / "platform.db")
    for number, resolution in (
        ("INC0000001", "Restore power via electrician"),
        ("INC0000002", "Restore power to network switch"),
        ("INC0000003", "Reconnect switch"),
        ("INC0000004", "Connect switch sole"),
    ):
        _insert_knowledge(
            store,
            number=number,
            pattern_key="network-switch-offline",
            label="Network switch offline",
            confidence=0.95,
            resolution=resolution,
            action=resolution,
            action_outcome="successful",
        )

    _insert_knowledge(
        store,
        number="INC0000005",
        pattern_key="network-node-offline",
        label="Network node offline",
        confidence=0.95,
        resolution="Network node self-recovered",
        action="Ping network device",
        action_outcome="successful",
    )
    _insert_knowledge(
        store,
        number="INC0000006",
        pattern_key="network-interface-down",
        label="Network interface down",
        confidence=0.0,
        resolution="Reset interface",
        action="Reset interface",
        action_outcome="successful",
    )

    count = rebuild_trusted_patterns(store, model_key=MODEL_KEY)
    assert count == 2

    switch = store.get_pattern("network-switch-offline")
    assert switch is not None
    assert switch["incident_count"] == 4
    assert switch["resolved_count"] == 4
    assert any(
        row == {
            "label": "Restore electrical power to network equipment",
            "count": 2,
            "incidents": ["INC0000001", "INC0000002"],
        }
        for row in switch["resolution_counts"]
    )
    assert any(
        row["label"] == "Reconnect network switch" and row["count"] == 2
        for row in switch["resolution_counts"]
    )

    node = store.get_pattern("network-node-offline")
    assert node is not None
    assert node["resolved_count"] == 0
    assert node["resolution_counts"] == []

    assert store.get_pattern("network-interface-down") is None

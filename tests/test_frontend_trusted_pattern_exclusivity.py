from src.knowledge.support_evidence import SupportEvidencePackage


def test_trusted_pattern_context_excludes_raw_fallback_and_graph_neighbours():
    package = SupportEvidencePackage(
        query="network switch offline",
        organisational_context=(
            "### Organisational pattern evidence\n"
            "Canonical issue pattern: Network switch offline\n"
            "Trusted resolution families:\n"
            "- Restore electrical power to network equipment: 3 incident(s)\n"
            "- Reconnect network switch: 2 incident(s)"
        ),
        incident_context=(
            "### Raw historical case evidence\n"
            "INC0067217 breaker tripping\n"
            "INC0085733 replacement outlier"
        ),
        graph_context="### Enriched support graph\n- unrelated raw neighbour",
        governed_context="Approved network safety guidance",
    )

    prompt = package.to_prompt_context()

    assert "Canonical issue pattern: Network switch offline" in prompt
    assert "Restore electrical power to network equipment" in prompt
    assert "Approved network safety guidance" in prompt
    assert "INC0067217" not in prompt
    assert "INC0085733" not in prompt
    assert "breaker tripping" not in prompt
    assert "unrelated raw neighbour" not in prompt


def test_raw_fallback_remains_available_without_trusted_pattern():
    package = SupportEvidencePackage(
        query="unknown symptom",
        incident_context="### Raw historical case evidence\nINC0000123 useful raw fallback",
        graph_context="### Enriched support graph\n- related device",
    )

    prompt = package.to_prompt_context()

    assert "INC0000123 useful raw fallback" in prompt
    assert "related device" in prompt

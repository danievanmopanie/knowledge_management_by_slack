from src.knowledge.support_extraction import (
    EXTRACTION_SCHEMA_VERSION,
    extraction_model_key,
    incident_extraction_text,
)
from src.reporting.incidents import Incident


def test_extraction_model_key_versions_the_knowledge_ontology():
    key = extraction_model_key()

    assert key.endswith(f"|{EXTRACTION_SCHEMA_VERSION}")
    assert "|v2" in key


def test_long_work_notes_cannot_crowd_out_resolution_evidence():
    long_work_notes = (
        "FIRST-WORK-NOTE "
        + ("intermediate troubleshooting detail " * 500)
        + " FINAL-WORK-NOTE-CONFIRMATION"
    )
    incident = Incident(
        number="INC0092846",
        short_description="Outlook keeps prompting for credentials",
        description="User changed the password but Outlook still prompts.",
        work_notes=long_work_notes,
        comments="Customer confirmed the issue after the password change.",
        resolution_notes=(
            "DISTINCT-RESOLUTION: removed the stale cached Office credential, "
            "signed in with the new password, and Outlook synchronised normally."
        ),
        state="Closed",
    )

    evidence = incident_extraction_text(incident)

    assert "DISTINCT-RESOLUTION" in evidence
    assert "FIRST-WORK-NOTE" in evidence
    assert "FINAL-WORK-NOTE-CONFIRMATION" in evidence
    assert "[middle truncated]" in evidence
    assert len(evidence) <= 12000

from datetime import datetime, timezone

from langchain_core.documents import Document

from src.knowledge.organisational_knowledge import (
    EnrichmentCandidate,
    OrganisationalKnowledgeRetriever,
    OrganisationalKnowledgeStore,
)
from src.knowledge.support_extraction import ExtractedAction, SupportExtraction
from src.knowledge.temporal_incidents import TemporalIncidentStore
from src.reporting.incidents import Incident


def _incident(number: str, *, location: str = "144 Oxford Rd Jhb") -> Incident:
    return Incident(
        number=number,
        short_description="Outlook repeatedly asks for password",
        description="After a password change Outlook prompts for credentials every time it opens.",
        work_notes="Password reset alone did not help. Stale Office credentials were then removed.",
        resolution_notes="Cleared stale Office credentials and Outlook signed in with the new password.",
        state="Closed",
        assignment_group="PT-IM-FLS-Oxford",
        assigned_to="Technician One",
        location=location,
        category="Application",
        subcategory="Outlook",
        opened_at=datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 8, 1, 8, 55, tzinfo=timezone.utc),
    )


def _extraction(*, resolution: bool = True) -> SupportExtraction:
    return SupportExtraction(
        issue_pattern="Microsoft Outlook repeated credential prompt",
        symptom="Outlook repeatedly prompts for credentials after a password change",
        technologies=["Microsoft Outlook", "Microsoft 365"],
        environment=["Windows endpoint"],
        actions=[
            ExtractedAction(
                action="Reset the password again but the prompt continued",
                canonical_action="Reset password only",
                outcome="failed",
                confidence=0.9,
            ),
            ExtractedAction(
                action="Removed stale Office credentials and signed in again",
                canonical_action="Clear cached Office credentials",
                outcome="successful" if resolution else "unknown",
                confidence=0.95,
            ),
        ],
        root_cause=(
            "A stale cached Office credential remained after the password changed"
            if resolution
            else ""
        ),
        root_cause_pattern="Stale cached authentication credential" if resolution else "",
        resolution=(
            "Removed the stale cached Office credential and Outlook accepted the new password"
            if resolution
            else ""
        ),
        resolution_pattern="Clear cached Office credentials" if resolution else "",
        confidence=0.9 if resolution else 0.65,
    )


def test_pending_enrichment_tracks_current_row_hash(tmp_path):
    db_path = tmp_path / "platform.db"
    temporal = TemporalIncidentStore(db_path)
    temporal.ingest_snapshot(
        [_incident("INC0092846")],
        source_file="incident (Aug_1).csv",
        observed_at="2026-08-01T10:00:00+00:00",
        complete_snapshot=False,
    )
    store = OrganisationalKnowledgeStore(db_path)

    pending = store.pending(limit=10, model="test-model")
    assert len(pending) == 1
    assert pending[0].incident.number == "INC0092846"
    assert store.pending_count(model="test-model") == 1

    store.upsert(pending[0], _extraction(), model="test-model")

    assert store.pending_count(model="test-model") == 0


def test_exact_enriched_context_explains_case_and_repeat_pattern(tmp_path):
    store = OrganisationalKnowledgeStore(tmp_path / "platform.db")
    for index, number in enumerate(("INC0092846", "INC0093989", "INC0094100")):
        store.upsert(
            EnrichmentCandidate(
                incident=_incident(number),
                row_hash=f"hash-{index}",
                source_file="incident.csv",
            ),
            _extraction(resolution=index < 2),
            model="test-model",
        )
    assert store.rebuild_patterns() == 1

    retriever = OrganisationalKnowledgeRetriever.__new__(OrganisationalKnowledgeRetriever)
    retriever.store = store
    retriever.index = None

    text = retriever.exact_context("INC0092846")

    assert "Microsoft Outlook repeated credential prompt" in text
    assert "Reset password only" in text
    assert "[failed]" in text
    assert "Clear cached Office credentials" in text
    assert "Known enriched incidents in this pattern: 3" in text
    assert "Incidents with recorded resolution knowledge: 2" in text
    assert "INC0093989" in text


class _FakeVectorStore:
    def similarity_search(self, query, k=5, where=None):
        assert where == {"knowledge_kind": "symptom"}
        return [
            Document(page_content="symptom", metadata={"number": "INC0092846", "score": 0.93}),
            Document(page_content="symptom", metadata={"number": "INC0093989", "score": 0.90}),
            Document(page_content="symptom", metadata={"number": "INC0094100", "score": 0.86}),
        ]


class _FakeIndex:
    vector_store = _FakeVectorStore()


def test_collective_context_counts_structured_knowledge_not_llm_guesses(tmp_path):
    store = OrganisationalKnowledgeStore(tmp_path / "platform.db")
    store.upsert(
        EnrichmentCandidate(_incident("INC0092846"), "h1", "a.csv"),
        _extraction(resolution=True),
        model="test-model",
    )
    store.upsert(
        EnrichmentCandidate(_incident("INC0093989"), "h2", "b.csv"),
        _extraction(resolution=True),
        model="test-model",
    )
    store.upsert(
        EnrichmentCandidate(_incident("INC0094100"), "h3", "c.csv"),
        _extraction(resolution=False),
        model="test-model",
    )

    retriever = OrganisationalKnowledgeRetriever(store=store, index=_FakeIndex())
    context = retriever.collective_context(
        "Outlook asks for password after password reset",
        min_similarity=0.4,
    )

    assert "Retrieved enriched incidents: 3" in context
    assert "Clear cached Office credentials: 2 incident(s)" in context
    assert "Reset password only: 3 incident(s)" in context
    assert "Observed failed actions / no-fix paths" in context
    assert "INC0092846" in context
    assert "INC0093989" in context
    assert "INC0094100" in context
    assert "LLM-estimated probabilities" in context

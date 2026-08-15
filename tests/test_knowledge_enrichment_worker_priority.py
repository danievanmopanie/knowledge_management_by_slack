from src.knowledge.organisational_knowledge import EnrichmentCandidate
from src.reporting.incidents import Incident
from src.worker.knowledge_enrichment_worker import _select_candidates


class _Store:
    def __init__(self, candidates):
        self.candidates = candidates

    def pending(self, *, limit, model):
        return self.candidates[:limit]


def _candidate(
    number: str,
    *,
    state: str = "In Progress",
    resolution: str = "",
    work_notes: str = "",
    description: str = "",
):
    return EnrichmentCandidate(
        incident=Incident(
            number=number,
            state=state,
            resolution_notes=resolution,
            work_notes=work_notes,
            description=description,
        ),
        row_hash=number,
        source_file="incident.csv",
    )


def test_backfill_prioritises_resolved_cases_with_real_resolution_evidence():
    thin_open = _candidate("INC0001", description="brief failure")
    rich_closed = _candidate(
        "INC0002",
        state="Closed",
        resolution="Cleared the stale credential cache and validated Outlook sign-in with the user.",
        work_notes="Detailed troubleshooting evidence. " * 20,
        description="Outlook repeatedly prompted for credentials after a password change." * 2,
    )
    resolved_without_resolution = _candidate(
        "INC0003",
        state="Resolved",
        work_notes="Technician investigated the endpoint. " * 10,
    )

    selected = _select_candidates(
        _Store([thin_open, resolved_without_resolution, rich_closed]),
        limit=2,
        model_key="model|v2",
    )

    assert [item.incident.number for item in selected] == ["INC0002", "INC0003"]

from src.knowledge import fast_incident_ingest as ingest
from src.knowledge.incident_dedupe import content_hash
from src.reporting.incidents import Incident


class FakeVectorStore:
    def __init__(self):
        self.added = []
        self.deleted = []
        self.metadata_updates = []

    def count(self):
        return 3

    def delete_documents(self, ids):
        self.deleted.extend(ids)

    def add_documents(self, documents, metadatas=None, ids=None, batch_size=None):
        self.added.extend(documents)
        return ids or []

    def update_metadatas(self, ids, metadatas):
        self.metadata_updates.extend(zip(ids, metadatas))


def test_lifecycle_only_change_updates_metadata_without_embedding(monkeypatch):
    inc = Incident(
        number="INC1",
        short_description="Outlook prompts for password",
        description="Repeated credential prompt",
        state="In Progress",
        assignment_group="Desktop",
    )
    existing = {"INC1": content_hash(inc)}
    saved = {}
    monkeypatch.setattr(ingest, "load_hash_index", lambda: dict(existing))
    monkeypatch.setattr(ingest, "save_hash_index", lambda index: saved.update(index))

    vector = FakeVectorStore()
    result = ingest.FastIncidentEmbedder(vector).index([inc])

    assert result.incidents_embedded == 0
    assert result.metadata_only_incidents == 1
    assert result.vector_documents == 0
    assert vector.added == []
    assert vector.deleted == []
    assert vector.metadata_updates
    assert saved["INC1"] == existing["INC1"]


def test_embedder_can_keep_hash_state_isolated(tmp_path):
    inc = Incident(
        number="INC2",
        short_description="Teams call drops",
        description="Call disconnects after several minutes",
    )
    hash_path = tmp_path / "benchmark" / "incident_hashes.json"
    vector = FakeVectorStore()
    embedder = ingest.FastIncidentEmbedder(vector, hash_path=hash_path)

    first = embedder.index([inc])
    second = embedder.index([inc])

    assert hash_path.exists()
    assert first.incidents_embedded == 1
    assert first.vector_documents > 0
    assert second.incidents_embedded == 0
    assert second.metadata_only_incidents == 1
    assert second.vector_documents == 0


def test_embedder_reports_real_document_progress(tmp_path):
    incidents = [
        Incident(
            number="INC10",
            short_description="Outlook search fails",
            description="Search returns no results",
            work_notes="Rebuilt local search index",
            resolution_notes="Search index rebuild restored results",
        ),
        Incident(
            number="INC11",
            short_description="Teams microphone unavailable",
            description="Microphone is not listed in Teams",
        ),
    ]
    events = []
    embedder = ingest.FastIncidentEmbedder(
        FakeVectorStore(),
        hash_path=tmp_path / "incident_hashes.json",
    )

    result = embedder.index(incidents, incident_chunk_size=1, on_progress=events.append)

    plan = next(event for event in events if event["stage"] == "embedding_plan")
    progress = [event for event in events if event["stage"] == "embedding_progress"]
    assert plan["documents_total"] == result.vector_documents
    assert progress
    assert progress[-1]["documents_done"] == result.vector_documents
    assert progress[-1]["documents_total"] == result.vector_documents

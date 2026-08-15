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

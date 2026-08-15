from types import SimpleNamespace

from src.worker import incident_ingest_worker as worker


class FakeStore:
    def __init__(self):
        self.succeeded = None
        self.failed = None

    def claim_next(self):
        return {
            "job_id": "ING-TEST",
            "file_path": "/tmp/june.csv",
            "file_name": "incident (Jun).csv",
            "channel_id": None,
            "thread_ts": None,
        }

    def mark_succeeded(self, job_id, metrics):
        self.succeeded = (job_id, metrics)

    def mark_failed(self, job_id, error_message):
        self.failed = (job_id, error_message)


def test_worker_uses_partial_snapshot_for_create_knowledge_uploads(monkeypatch):
    captured = {}

    class FakeIngestor:
        def ingest_csv(self, path, *, complete_snapshot=True):
            captured["path"] = path
            captured["complete_snapshot"] = complete_snapshot
            return SimpleNamespace(
                as_dict=lambda: {
                    "rows": 1,
                    "unique_incidents": 1,
                    "new": 1,
                    "changed": 0,
                    "unchanged": 0,
                    "versions_written": 1,
                    "transitions_written": 0,
                    "dwell_rows_written": 0,
                    "vector_documents": 1,
                    "embedding_documents_per_second": 1.0,
                    "graph_nodes_added": 1,
                    "graph_edges_added": 1,
                    "parse_seconds": 0.0,
                    "temporal_seconds": 0.0,
                    "embedding_seconds": 0.0,
                    "graph_seconds": 0.0,
                    "total_seconds": 0.0,
                }
            )

    monkeypatch.setattr(worker, "FastTemporalIncidentIngestor", FakeIngestor)
    monkeypatch.setattr(worker, "_client", lambda: None)

    store = FakeStore()
    assert worker.run_once(store) is True
    assert captured["complete_snapshot"] is False
    assert store.failed is None
    assert store.succeeded is not None

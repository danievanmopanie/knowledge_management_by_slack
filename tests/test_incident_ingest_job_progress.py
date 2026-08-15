from src.knowledge.incident_ingest_jobs import IncidentIngestJobStore


def test_ingest_job_persists_progress_message_and_state(tmp_path):
    store = IncidentIngestJobStore(tmp_path / "platform.db")
    job_id = store.enqueue(
        file_path="/tmp/incidents.csv",
        file_name="incident (Jul).csv",
        requester_id="U1",
        channel_id="C1",
        thread_ts="T1",
    )

    store.set_progress_message(job_id, "171234.567")
    store.update_progress(
        job_id,
        {
            "stage": "embedding_progress",
            "documents_done": 500,
            "documents_total": 1000,
        },
    )

    job = store.get(job_id)
    assert job is not None
    assert job["progress_message_ts"] == "171234.567"
    assert job["progress"]["stage"] == "embedding_progress"
    assert job["progress"]["documents_done"] == 500
    assert job["progress"]["documents_total"] == 1000

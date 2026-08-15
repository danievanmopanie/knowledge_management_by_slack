from src.bot.create_knowledge_progress import build_blocks, queued_blocks


def _text(blocks):
    values = []
    for block in blocks:
        text = block.get("text") or {}
        if isinstance(text, dict):
            values.append(str(text.get("text") or ""))
        for element in block.get("elements") or []:
            if isinstance(element, dict):
                values.append(str(element.get("text") or ""))
    return "\n".join(values)


def _job():
    return {
        "job_id": "ING-ABC123",
        "file_name": "incident (Jul).csv",
        "channel_id": "C1",
        "thread_ts": "T1",
    }


def test_queued_card_shows_full_build_process():
    blocks = queued_blocks(_job())
    text = _text(blocks)

    assert "Queued" in text
    assert "Upload profiled and confirmed" in text
    assert "Parse ServiceNow snapshot" in text
    assert "Compare with existing knowledge" in text
    assert "Build temporal history" in text
    assert "Build knowledge graph" in text
    assert "Searchable embeddings" in text
    assert "Validate and publish" in text


def test_running_card_shows_real_embedding_progress():
    progress = {
        "stage": "embedding_progress",
        "rows": 16000,
        "unique_incidents": 15800,
        "new": 4000,
        "changed": 3000,
        "unchanged": 8800,
        "versions_written": 7000,
        "transitions_written": 1200,
        "dwell_rows_written": 500,
        "graph_nodes_added": 2100,
        "graph_edges_added": 18000,
        "documents_done": 5000,
        "documents_total": 10000,
        "metadata_only_incidents": 900,
    }
    text = _text(build_blocks(_job(), progress))

    assert "4,000 new" in text
    assert "3,000 changed" in text
    assert "8,800 unchanged" in text
    assert "50%" in text
    assert "5,000 / 10,000 documents" in text


def test_completed_card_explains_knowledge_created():
    metrics = {
        "new": 4000,
        "changed": 3000,
        "unchanged": 8800,
        "versions_written": 7000,
        "transitions_written": 1200,
        "dwell_rows_written": 500,
        "graph_nodes_added": 2100,
        "graph_edges_added": 18000,
        "vector_incidents": 2100,
        "vector_metadata_only_incidents": 900,
        "vector_documents": 5200,
        "vector_collection_total": 41574,
        "embedding_documents_per_second": 63.2,
        "embedding_seconds": 82.3,
        "graph_seconds": 1.9,
        "total_seconds": 85.1,
    }
    text = _text(build_blocks(_job(), {"stage": "completed", "metrics": metrics}))

    assert "Knowledge build complete" in text
    assert "Knowledge created" in text
    assert "1,200 transitions" in text
    assert "+18,000 relationships" in text
    assert "5,200 vector documents processed" in text
    assert "900 lifecycle-only incidents" in text

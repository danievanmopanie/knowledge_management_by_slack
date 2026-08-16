"""Tests for remembering which governed document was last cited to a Slack thread."""

from src.knowledge.citation_memory import CitationMemory


def test_record_and_get_roundtrip(tmp_path):
    memory = CitationMemory(tmp_path / "platform.db")
    assert memory.get("C1", "100.1") is None

    memory.record(channel_id="C1", thread_ts="100.1", document_id="doc_abc", title="VPN Runbook")
    cited = memory.get("C1", "100.1")

    assert cited["document_id"] == "doc_abc"
    assert cited["title"] == "VPN Runbook"


def test_record_overwrites_previous_citation_for_the_same_thread(tmp_path):
    memory = CitationMemory(tmp_path / "platform.db")
    memory.record(channel_id="C1", thread_ts="100.1", document_id="doc_old", title="Old Article")
    memory.record(channel_id="C1", thread_ts="100.1", document_id="doc_new", title="New Article")

    cited = memory.get("C1", "100.1")

    assert cited["document_id"] == "doc_new"
    assert cited["title"] == "New Article"


def test_record_with_empty_document_id_is_ignored(tmp_path):
    memory = CitationMemory(tmp_path / "platform.db")
    memory.record(channel_id="C1", thread_ts="100.1", document_id="", title="Untitled")

    assert memory.get("C1", "100.1") is None


def test_citations_are_scoped_per_thread(tmp_path):
    memory = CitationMemory(tmp_path / "platform.db")
    memory.record(channel_id="C1", thread_ts="100.1", document_id="doc_a", title="Article A")
    memory.record(channel_id="C1", thread_ts="100.2", document_id="doc_b", title="Article B")

    assert memory.get("C1", "100.1")["document_id"] == "doc_a"
    assert memory.get("C1", "100.2")["document_id"] == "doc_b"

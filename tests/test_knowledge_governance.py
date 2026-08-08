"""Milestone 0 tests for knowledge governance and durable platform state."""

from pathlib import Path

import pytest

from src.core.audit import AuditStore
from src.core.context import RequestContext
from src.knowledge.catalog import KnowledgeCatalog
from src.knowledge.file_loader import UploadValidationError, validate_slack_file
from src.knowledge.governed_ingest import commit_knowledge
from src.knowledge.staging import StagingStore
from src.security.access import can_read


class FakeVectorStore:
    def __init__(self):
        self.added: list[str] = []
        self.deleted: list[str] = []

    def add_documents(self, documents, metadatas=None, ids=None, batch_size=None):
        self.added.extend(ids or [])
        return ids or []

    def delete_documents(self, ids):
        self.deleted.extend(ids)


def test_upload_validation_rejects_unsupported_and_oversized(monkeypatch):
    monkeypatch.setattr("src.knowledge.file_loader.settings.max_upload_bytes", 100)
    with pytest.raises(UploadValidationError):
        validate_slack_file({"id": "F1", "name": "malware.exe", "size": 10})
    with pytest.raises(UploadValidationError):
        validate_slack_file({"id": "F2", "name": "notes.md", "size": 101})

    safe_name, suffix = validate_slack_file(
        {"id": "F3", "name": "../../Useful Runbook.md", "size": 99}
    )
    assert safe_name.startswith("F3_")
    assert "/" not in safe_name
    assert suffix == ".md"


def test_staging_does_not_create_catalogue_entry(tmp_path: Path):
    db = tmp_path / "platform.db"
    staging = StagingStore(db)
    catalog = KnowledgeCatalog(db)
    stage_id = staging.create(
        slack_file_id="F1",
        file_name="runbook.md",
        local_path=str(tmp_path / "runbook.md"),
        uploader_id="U1",
        channel_id="C1",
    )
    assert staging.get(stage_id)["status"] == "staged"
    assert catalog.get_document("doc_nonexistent") is None


def test_catalogued_ingest_is_idempotent_and_versions_changes(tmp_path: Path):
    db = tmp_path / "platform.db"
    catalog = KnowledgeCatalog(db)
    vectors = FakeVectorStore()
    first = commit_knowledge(
        text="Reset VPN by restarting the client.",
        title="VPN Runbook",
        source_id="F1",
        owner_id="U1",
        catalog=catalog,
        vector_store=vectors,
    )
    added_after_first = list(vectors.added)
    second = commit_knowledge(
        text="Reset VPN by restarting the client.",
        title="VPN Runbook",
        source_id="F1",
        owner_id="U1",
        catalog=catalog,
        vector_store=vectors,
    )
    assert second["unchanged"] is True
    assert vectors.added == added_after_first

    third = commit_knowledge(
        text="Reset VPN, then validate MFA before escalating.",
        title="VPN Runbook",
        source_id="F1",
        owner_id="U1",
        catalog=catalog,
        vector_store=vectors,
    )
    assert third["unchanged"] is False
    assert set(vectors.deleted) == set(first["chunk_ids"])
    assert third["version_id"] != first["version_id"]


def test_restricted_acl_denies_by_default_and_supports_identity_scope():
    restricted = {
        "visibility": "restricted",
        "allowed_user_ids": "U-ALLOWED",
        "allowed_channel_ids": "C-SECURE",
        "allowed_roles": "knowledge-owner",
        "allowed_groups": "frontend-support",
        "allowed_sites": "Amandelbult",
    }
    denied = RequestContext.from_slack(channel_id="C-OTHER", user_id="U-DENIED")
    allowed_user = RequestContext.from_slack(channel_id="C-OTHER", user_id="U-ALLOWED")
    allowed_channel = RequestContext.from_slack(channel_id="C-SECURE", user_id="U-DENIED")
    allowed_role = RequestContext.from_slack(
        channel_id="C-OTHER", user_id="U2", roles=["knowledge-owner"]
    )
    allowed_group = RequestContext.from_slack(
        channel_id="C-OTHER", user_id="U3", groups=["frontend-support"]
    )
    allowed_site = RequestContext.from_slack(
        channel_id="C-OTHER", user_id="U4", site="Amandelbult"
    )

    assert can_read(restricted, None) is False
    assert can_read(restricted, denied) is False
    assert can_read(restricted, allowed_user) is True
    assert can_read(restricted, allowed_channel) is True
    assert can_read(restricted, allowed_role) is True
    assert can_read(restricted, allowed_group) is True
    assert can_read(restricted, allowed_site) is True
    assert can_read({"visibility": "internal"}, denied) is True


def test_audit_events_are_durable_and_queryable(tmp_path: Path):
    db = tmp_path / "platform.db"
    audit = AuditStore(db)
    context = RequestContext.from_slack(
        channel_id="C1",
        user_id="U1",
        thread_ts="123.45",
        request_id="req-123",
    )
    audit.record(
        context,
        action="knowledge.confirm",
        outcome="success",
        target_type="document",
        target_id="doc-1",
        metadata={"chunks": 3},
    )
    by_request = audit.by_request("req-123")
    by_actor = audit.by_actor("U1")
    assert len(by_request) == 1
    assert by_request[0]["action"] == "knowledge.confirm"
    assert by_actor[0]["request_id"] == "req-123"
    assert "Reset VPN" not in by_request[0]["metadata_json"]

import pytest

from src.knowledge.catalog import KnowledgeCatalog, StaleKnowledgeVersionError


def test_revision_compare_and_swap_preserves_article_identity(tmp_path):
    catalog = KnowledgeCatalog(tmp_path / "platform.db")
    first = catalog.register_version(
        source_system="legacy_kb",
        source_id="KB001",
        title="Laptop Audio",
        text="Original article",
        owner_id="U_OWNER",
    )

    second = catalog.register_version(
        source_system="legacy_kb",
        source_id="KB001",
        title="Laptop Audio",
        text="Revised article",
        owner_id="U_OWNER",
        expected_version_id=first["version_id"],
    )

    assert second["document_id"] == first["document_id"]
    assert second["version_id"] != first["version_id"]
    assert catalog.active_version(first["document_id"])["version_id"] == second["version_id"]


def test_stale_revision_is_rejected_without_advancing_active_version(tmp_path):
    catalog = KnowledgeCatalog(tmp_path / "platform.db")
    first = catalog.register_version(
        source_system="legacy_kb",
        source_id="KB001",
        title="Laptop Audio",
        text="Version one",
        owner_id="U_OWNER",
    )
    second = catalog.register_version(
        source_system="legacy_kb",
        source_id="KB001",
        title="Laptop Audio",
        text="Version two",
        owner_id="U_OWNER",
        expected_version_id=first["version_id"],
    )

    with pytest.raises(StaleKnowledgeVersionError):
        catalog.register_version(
            source_system="legacy_kb",
            source_id="KB001",
            title="Laptop Audio",
            text="Stale proposed version",
            owner_id="U_OWNER",
            expected_version_id=first["version_id"],
        )

    active = catalog.active_version(first["document_id"])
    assert active is not None
    assert active["version_id"] == second["version_id"]

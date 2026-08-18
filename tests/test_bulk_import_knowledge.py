import importlib.util
from pathlib import Path


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "bulk_import_knowledge.py"
_spec = importlib.util.spec_from_file_location("bulk_import_knowledge", _SCRIPT_PATH)
bulk_import_knowledge = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(bulk_import_knowledge)


def test_bulk_import_only_processes_supported_non_image_files(tmp_path, monkeypatch):
    source = tmp_path / "existing-kb"
    source.mkdir()
    (source / "vpn-runbook.md").write_text("Reset the VPN client.", encoding="utf-8")
    (source / "printer-guide.txt").write_text("Reseat the USB cable.", encoding="utf-8")
    (source / "photo.png").write_bytes(b"not-an-image")
    (source / "archive.bin").write_bytes(b"ignored")

    calls = []

    def fake_commit_knowledge(**kwargs):
        calls.append(kwargs)
        return {
            "document_id": f"doc-{len(calls)}",
            "version_id": f"v{len(calls)}",
            "chunks": 1,
            "unchanged": False,
        }

    monkeypatch.setattr(bulk_import_knowledge, "commit_knowledge", fake_commit_knowledge)

    counts = bulk_import_knowledge.import_directory(source)

    assert counts == {
        "candidates": 2,
        "imported": 2,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
    }
    assert {call["source_id"] for call in calls} == {
        "bulk-import:printer-guide.txt",
        "bulk-import:vpn-runbook.md",
    }
    assert all(call["source_system"] == "bulk-import" for call in calls)
    assert all(call["owner_id"] is None for call in calls)


def test_bulk_import_counts_unchanged_without_special_processing(tmp_path, monkeypatch):
    source = tmp_path / "existing-kb"
    source.mkdir()
    (source / "vpn-runbook.md").write_text("Reset the VPN client.", encoding="utf-8")

    def fake_commit_knowledge(**kwargs):
        return {
            "document_id": "doc-1",
            "version_id": "v1",
            "chunks": 3,
            "unchanged": True,
        }

    monkeypatch.setattr(bulk_import_knowledge, "commit_knowledge", fake_commit_knowledge)

    counts = bulk_import_knowledge.import_directory(source)

    assert counts["candidates"] == 1
    assert counts["unchanged"] == 1
    assert counts["imported"] == 0
    assert counts["failed"] == 0


def test_bulk_import_dry_run_never_extracts_or_commits(tmp_path, monkeypatch):
    source = tmp_path / "existing-kb"
    source.mkdir()
    (source / "article.md").write_text("Article", encoding="utf-8")

    def should_not_run(*args, **kwargs):
        raise AssertionError("dry-run must not extract or commit")

    monkeypatch.setattr(bulk_import_knowledge, "extract_text", should_not_run)
    monkeypatch.setattr(bulk_import_knowledge, "commit_knowledge", should_not_run)

    counts = bulk_import_knowledge.import_directory(source, dry_run=True)

    assert counts["candidates"] == 1
    assert counts["imported"] == 0
    assert counts["unchanged"] == 0
    assert counts["failed"] == 0

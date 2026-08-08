"""Backup and restore for the hybrid knowledge base (vector store, graph, raw docs)."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.config import settings

logger = logging.getLogger(__name__)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def create_backup(
    backup_root: Path | None = None,
    include_raw: bool | None = None,
    label: str | None = None,
) -> Path:
    """
    Create a full backup of the knowledge base.

    Includes:
    - Vector store (Chroma persistent data)
    - Knowledge graph (graph.json)
    - Optionally the raw uploaded documents

    Returns the path to the created backup directory.
    """
    backup_root = backup_root or settings.backup_root
    include_raw = settings.backup_include_raw if include_raw is None else include_raw
    backup_root.mkdir(parents=True, exist_ok=True)

    stamp = _timestamp()
    name = f"kb_backup_{stamp}"
    if label:
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        name = f"{name}_{safe_label}"

    backup_dir = backup_root / name
    backup_dir.mkdir(parents=True, exist_ok=False)

    manifest: dict[str, Any] = {
        "created_at": stamp,
        "label": label,
        "components": {},
    }

    # 1. Vector store (Chroma)
    vector_src = settings.vectorstore_path
    if vector_src.exists():
        vector_dst = backup_dir / "vectorstore"
        shutil.copytree(vector_src, vector_dst)
        manifest["components"]["vectorstore"] = str(vector_dst.relative_to(backup_dir))
        logger.info("Backed up vector store → %s", vector_dst)
    else:
        logger.warning("Vector store path does not exist: %s", vector_src)

    # 2. Graph store
    graph_src = settings.vectorstore_path / "graph.json"
    if graph_src.exists():
        graph_dst = backup_dir / "graph.json"
        shutil.copy2(graph_src, graph_dst)
        manifest["components"]["graph"] = "graph.json"
        logger.info("Backed up knowledge graph → %s", graph_dst)

    # 3. Raw documents (optional)
    if include_raw:
        raw_src = settings.raw_docs_path
        if raw_src.exists() and any(raw_src.iterdir()):
            raw_dst = backup_dir / "raw"
            shutil.copytree(raw_src, raw_dst)
            manifest["components"]["raw"] = "raw"
            logger.info("Backed up raw documents → %s", raw_dst)

    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info("Backup complete: %s", backup_dir)
    return backup_dir


def list_backups(backup_root: Path | None = None) -> list[dict[str, Any]]:
    """List available backups (newest first)."""
    backup_root = backup_root or settings.backup_root
    if not backup_root.exists():
        return []

    results = []
    for path in sorted(backup_root.iterdir(), reverse=True):
        if not path.is_dir() or not path.name.startswith("kb_backup_"):
            continue
        manifest_path = path / "manifest.json"
        info: dict[str, Any] = {"path": str(path), "name": path.name}
        if manifest_path.exists():
            try:
                info["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                info["manifest"] = None
        results.append(info)
    return results


def prune_backups(
    keep: int | None = None,
    backup_root: Path | None = None,
) -> list[str]:
    """
    Delete older backups, keeping only the most recent `keep` backups.

    Returns the list of deleted backup directory names.
    """
    keep = settings.backup_retention_count if keep is None else keep
    backups = list_backups(backup_root=backup_root)

    if len(backups) <= keep:
        return []

    to_delete = backups[keep:]  # newest-first
    deleted = []
    for item in to_delete:
        path = Path(item["path"])
        try:
            shutil.rmtree(path)
            deleted.append(path.name)
            logger.info("Pruned old backup: %s", path)
        except Exception:
            logger.exception("Failed to prune backup %s", path)

    return deleted


def restore_backup(
    backup_path: Path,
    restore_vector: bool = True,
    restore_graph: bool = True,
    restore_raw: bool = False,
) -> dict[str, Any]:
    """
    Restore a previously created backup.

    WARNING: This overwrites the current vector store / graph / raw data
    for the components that are restored.
    """
    backup_path = Path(backup_path)
    if not backup_path.exists() or not backup_path.is_dir():
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    manifest_path = backup_path / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    restored = []

    if restore_vector:
        src = backup_path / "vectorstore"
        if src.exists():
            dst = settings.vectorstore_path
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            restored.append("vectorstore")
            logger.info("Restored vector store from %s", src)
        else:
            logger.warning("No vectorstore component in backup %s", backup_path)

    if restore_graph:
        src = backup_path / "graph.json"
        if src.exists():
            dst = settings.vectorstore_path / "graph.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append("graph")
            logger.info("Restored knowledge graph from %s", src)

    if restore_raw:
        src = backup_path / "raw"
        if src.exists():
            dst = settings.raw_docs_path
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            restored.append("raw")
            logger.info("Restored raw documents from %s", src)

    return {
        "backup": str(backup_path),
        "restored": restored,
        "manifest": manifest,
    }


def run_scheduled_backup() -> dict[str, Any]:
    """
    Create a scheduled backup and prune old ones according to retention policy.

    Intended to be called by cron / systemd timer.
    """
    label = settings.backup_label_prefix
    backup_path = create_backup(label=label)
    deleted = prune_backups()
    return {
        "backup": str(backup_path),
        "pruned": deleted,
        "retention": settings.backup_retention_count,
    }

"""Bulk-onboard an existing formal knowledge base into governed knowledge.

This CLI is deliberately deterministic and model-free. It extracts supported
non-image files and commits them through the normal governed/versioned ingest
path. Re-running the same content is cheap: ``commit_knowledge`` detects the
content hash and skips vector replacement for unchanged articles.

Examples:
    python scripts/bulk_import_knowledge.py
    python scripts/bulk_import_knowledge.py --source ./data/raw/existing-kb
    python scripts/bulk_import_knowledge.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from src.core.config import settings
from src.knowledge.file_loader import (
    IMAGE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    UploadValidationError,
    extract_text,
)
from src.knowledge.governed_ingest import commit_knowledge

logger = logging.getLogger(__name__)

IMPORTABLE_EXTENSIONS = SUPPORTED_EXTENSIONS - IMAGE_EXTENSIONS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk-onboard formal knowledge files")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Directory to import from (defaults to RAW_DOCS_PATH)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidate files without extracting, embedding or writing anything",
    )
    return parser.parse_args()


def iter_importable_files(source: Path):
    """Yield supported non-image files in deterministic relative-path order."""
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix().lower()):
        if path.is_file() and path.suffix.lower() in IMPORTABLE_EXTENSIONS:
            yield path


def import_directory(source: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Import one directory tree and return compact operational counters."""
    files = list(iter_importable_files(source))
    counts = {
        "candidates": len(files),
        "imported": 0,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
    }

    for path in files:
        relative = path.relative_to(source)
        if dry_run:
            print(f"  would import: {relative}", flush=True)
            continue

        try:
            text = extract_text(path)
            if not text.strip():
                counts["skipped"] += 1
                print(f"  skipped (no extractable text): {relative}", flush=True)
                continue
            result = commit_knowledge(
                text=text,
                title=str(relative),
                source_id=f"bulk-import:{relative.as_posix()}",
                source_system="bulk-import",
                owner_id=None,
            )
        except UploadValidationError as exc:
            counts["skipped"] += 1
            print(f"  skipped ({exc}): {relative}", flush=True)
            continue
        except Exception:
            logger.exception("Bulk import failed for %s", path)
            counts["failed"] += 1
            print(f"  failed: {relative}", flush=True)
            continue

        if result["unchanged"]:
            counts["unchanged"] += 1
            print(f"  unchanged: {relative}", flush=True)
        else:
            counts["imported"] += 1
            print(f"  imported: {relative} -> {result['chunks']} chunk(s)", flush=True)

    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args()
    source = (args.source or settings.raw_docs_path).resolve()

    if not source.exists() or not source.is_dir():
        print(f"Source directory does not exist or is not a directory: {source}", flush=True)
        return

    print(
        "Bulk knowledge import starting\n"
        f"  source: {source}\n"
        f"  dry_run: {args.dry_run}",
        flush=True,
    )
    counts = import_directory(source, dry_run=args.dry_run)
    print(
        "\nBulk knowledge import complete: "
        f"candidates={counts['candidates']} imported={counts['imported']} "
        f"unchanged={counts['unchanged']} skipped={counts['skipped']} "
        f"failed={counts['failed']}",
        flush=True,
    )


if __name__ == "__main__":
    main()

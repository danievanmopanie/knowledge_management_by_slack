"""Bulk-onboard an existing formal knowledge base into governed knowledge.

Drop existing KB exports (markdown, text, PDF, DOCX, ...) into the configured
raw-docs directory (`RAW_DOCS_PATH`, default `./data/raw`) and run this once to
make them retrievable evidence for #frontend-support, instead of onboarding one
file at a time through Slack. Safe to re-run: commit_knowledge() is content-hash
versioned, so an unchanged file is skipped and only a changed file gets a new
version.

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
from src.knowledge.file_loader import SUPPORTED_EXTENSIONS, IMAGE_EXTENSIONS, UploadValidationError, extract_text
from src.knowledge.governed_ingest import commit_knowledge

logger = logging.getLogger(__name__)

IMPORTABLE_EXTENSIONS = SUPPORTED_EXTENSIONS - IMAGE_EXTENSIONS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Directory to import from (defaults to RAW_DOCS_PATH)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be imported without writing to the knowledge base",
    )
    return parser.parse_args()


def _iter_importable_files(source: Path):
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMPORTABLE_EXTENSIONS:
            yield path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args()
    source = args.source or settings.raw_docs_path

    if not source.exists():
        print(f"Source directory does not exist: {source}", flush=True)
        return

    files = list(_iter_importable_files(source))
    print(
        f"Bulk knowledge import starting\n"
        f"  source: {source}\n"
        f"  candidate_files: {len(files)}\n"
        f"  dry_run: {args.dry_run}\n",
        flush=True,
    )
    if not files:
        print("No importable files found (supported: "
              f"{', '.join(sorted(IMPORTABLE_EXTENSIONS))}).", flush=True)
        return

    imported = 0
    unchanged = 0
    failed = 0
    for path in files:
        relative = path.relative_to(source)
        if args.dry_run:
            print(f"  would import: {relative}", flush=True)
            continue
        try:
            text = extract_text(path)
            if not text.strip():
                print(f"  skipped (no extractable text): {relative}", flush=True)
                continue
            result = commit_knowledge(
                text=text,
                title=str(relative),
                source_id=f"bulk-import:{relative.as_posix()}",
                owner_id=None,
            )
        except UploadValidationError as exc:
            print(f"  skipped ({exc}): {relative}", flush=True)
            continue
        except Exception:
            logger.exception("Bulk import failed for %s", path)
            failed += 1
            print(f"  failed: {relative}", flush=True)
            continue

        if result["unchanged"]:
            unchanged += 1
            print(f"  unchanged: {relative}", flush=True)
        else:
            imported += 1
            print(f"  imported: {relative} -> {result['chunks']} chunk(s)", flush=True)

    print(
        "\nBulk knowledge import complete: "
        f"imported={imported} unchanged={unchanged} failed={failed} "
        f"total_candidates={len(files)}",
        flush=True,
    )


if __name__ == "__main__":
    main()

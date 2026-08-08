#!/usr/bin/env python3
"""CLI for backing up and restoring the knowledge base."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is on the path when run as a script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.backup import create_backup, list_backups, restore_backup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backup_kb")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backup / restore the hybrid knowledge base (vector + graph + raw)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # backup
    p_backup = sub.add_parser("backup", help="Create a new backup")
    p_backup.add_argument(
        "--label",
        type=str,
        default=None,
        help="Optional label for the backup (e.g. pre-migration)",
    )
    p_backup.add_argument(
        "--no-raw",
        action="store_true",
        help="Do not include raw uploaded documents",
    )
    p_backup.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Backup root directory (default: ./data/backups)",
    )

    # list
    p_list = sub.add_parser("list", help="List available backups")
    p_list.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Backup root directory (default: ./data/backups)",
    )

    # restore
    p_restore = sub.add_parser("restore", help="Restore a backup")
    p_restore.add_argument(
        "backup_path",
        type=Path,
        help="Path to the backup directory to restore",
    )
    p_restore.add_argument(
        "--no-vector",
        action="store_true",
        help="Skip restoring the vector store",
    )
    p_restore.add_argument(
        "--no-graph",
        action="store_true",
        help="Skip restoring the knowledge graph",
    )
    p_restore.add_argument(
        "--raw",
        action="store_true",
        help="Also restore raw documents (overwrites current raw folder)",
    )

    args = parser.parse_args()

    if args.command == "backup":
        path = create_backup(
            backup_root=args.dir,
            include_raw=not args.no_raw,
            label=args.label,
        )
        print(f"\nBackup created: {path}")

    elif args.command == "list":
        backups = list_backups(backup_root=args.dir)
        if not backups:
            print("No backups found.")
            return
        print(f"Found {len(backups)} backup(s):\n")
        for b in backups:
            manifest = b.get("manifest") or {}
            created = manifest.get("created_at", "?")
            label = manifest.get("label") or "-"
            components = ", ".join((manifest.get("components") or {}).keys()) or "?"
            print(f"  {b['name']}")
            print(f"    created : {created}")
            print(f"    label   : {label}")
            print(f"    content : {components}")
            print(f"    path    : {b['path']}\n")

    elif args.command == "restore":
        result = restore_backup(
            backup_path=args.backup_path,
            restore_vector=not args.no_vector,
            restore_graph=not args.no_graph,
            restore_raw=args.raw,
        )
        print(f"\nRestored from: {result['backup']}")
        print(f"Components  : {', '.join(result['restored']) or 'none'}")


if __name__ == "__main__":
    main()

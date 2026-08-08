"""Content-hash deduplication for daily ServiceNow incident CSV uploads.

Avoids re-embedding rows that have not changed since the last index.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Iterable

from src.core.config import settings
from src.reporting.incidents import Incident

logger = logging.getLogger(__name__)

# Fields that drive the embedding / meaning of an incident.
# If any of these change, we re-embed. Purely operational churn is ignored
# only when the listed fields are identical.
HASH_FIELDS = (
    "short_description",
    "description",
    "work_notes",
    "comments",
    "resolution_notes",
    "state",
    "assignment_group",
    "assigned_to",
    "category",
    "subcategory",
    "location",
)


def _default_hash_path() -> Path:
    return Path(settings.vectorstore_path).parent / "incident_content_hashes.json"


def content_hash(inc: Incident) -> str:
    """Stable SHA-256 of the fields that matter for embedding."""
    parts: list[str] = [f"number={inc.number}"]
    for name in HASH_FIELDS:
        value = getattr(inc, name, "") or ""
        # Normalise whitespace so trivial export differences do not force re-embed
        normalised = " ".join(str(value).split())
        parts.append(f"{name}={normalised}")
    payload = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_hash_index(path: Path | None = None) -> dict[str, str]:
    """Load {incident_number: content_hash} from disk."""
    path = path or _default_hash_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        logger.exception("Failed to load incident hash index from %s", path)
    return {}


def save_hash_index(index: dict[str, str], path: Path | None = None) -> None:
    path = path or _default_hash_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")


def filter_changed_incidents(
    incidents: Iterable[Incident],
    existing: dict[str, str] | None = None,
) -> tuple[list[Incident], list[Incident], dict[str, str]]:
    """
    Split incidents into changed (need embed) vs unchanged (skip).

    Returns:
      changed, unchanged, updated_index
    """
    index = dict(existing if existing is not None else load_hash_index())
    changed: list[Incident] = []
    unchanged: list[Incident] = []

    for inc in incidents:
        h = content_hash(inc)
        prev = index.get(inc.number)
        if prev == h:
            unchanged.append(inc)
        else:
            changed.append(inc)
            index[inc.number] = h

    logger.info(
        "Incident dedupe: %s changed, %s unchanged (skip re-embed)",
        len(changed),
        len(unchanged),
    )
    return changed, unchanged, index

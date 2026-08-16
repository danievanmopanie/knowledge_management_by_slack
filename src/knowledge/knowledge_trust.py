"""Deterministic trust and canonicalisation rules for enriched support knowledge.

LLM extraction is evidence interpretation, not the final organisational truth. These
helpers decide what may contribute to cross-incident rollups and collapse only
obvious wording variants into reusable families. Exact incident evidence remains
untouched and auditable.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from src.core.config import settings


def _space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value: Any) -> str:
    text = _space(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def safe_confidence(value: Any, *, default: float = 0.0) -> float:
    """Return bounded confidence; malformed/null model output becomes untrusted."""
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def is_trusted_confidence(value: Any, *, threshold: float | None = None) -> bool:
    cutoff = settings.knowledge_min_extraction_confidence if threshold is None else threshold
    return safe_confidence(value) >= float(cutoff)


# Conservative lexical families: no semantic guessing is allowed in this layer.
_POWER_TERMS = (
    "restore power",
    "power restoration",
    "restore electrical power",
    "electrician for power",
    "electrician to restore power",
    "power transformer issue",
    "restore supply",
    "electrical supply",
)
_RECONNECT_TERMS = (
    "reconnect switch",
    "connect switch",
    "reconnect network switch",
    "connect network switch",
)
_PASSIVE_OUTCOME_TERMS = (
    "self recovered",
    "self-recovered",
    "recovered itself",
    "auto recovered",
    "automatically recovered",
    "issue no longer present",
    "working again without",
    "no intervention",
)
_GENERIC_OUTCOME_TERMS = (
    "user confirmed working",
    "confirmed working",
    "issue resolved",
    "ticket closed",
    "incident closed",
)


def canonical_resolution_family(value: Any) -> str:
    """Collapse only operationally obvious wording variants."""
    text = _space(value)
    key = _key(text)
    if not key:
        return ""
    if any(_key(term) in key for term in _POWER_TERMS):
        return "Restore electrical power to network equipment"
    if any(_key(term) in key for term in _RECONNECT_TERMS):
        return "Reconnect network switch"
    return text


def canonical_action_family(value: Any) -> str:
    return canonical_resolution_family(value)


def is_passive_outcome(value: Any) -> bool:
    key = _key(value)
    return bool(key) and any(_key(term) in key for term in _PASSIVE_OUTCOME_TERMS)


def is_generic_outcome(value: Any) -> bool:
    key = _key(value)
    return bool(key) and any(_key(term) in key for term in _GENERIC_OUTCOME_TERMS)


def is_reusable_resolution(value: Any) -> bool:
    """Reusable resolution must describe an intervention, not merely an outcome."""
    text = _space(value)
    return bool(text) and not is_passive_outcome(text) and not is_generic_outcome(text)


def is_trusted_item(item: Any, *, model_key: str) -> bool:
    return bool(
        item
        and str(getattr(item, "model", "")) == model_key
        and is_trusted_confidence(getattr(item, "confidence", 0.0))
    )


def index_trusted_items(index: Any, items: Iterable[Any], *, model_key: str) -> dict[str, int]:
    """Remove stale docs for every item, then index only current-schema trusted items."""
    values = list(items)
    stale_ids: list[str] = []
    trusted: list[Any] = []
    for item in values:
        number = str(getattr(item, "incident_number", ""))
        if number:
            stale_ids.extend(
                [
                    f"support-{number}-symptom",
                    f"support-{number}-resolution",
                    f"support-{number}-actions",
                ]
            )
        if is_trusted_item(item, model_key=model_key):
            trusted.append(item)
    if stale_ids:
        index.vector_store.delete_documents(stale_ids)
    if not trusted:
        return {"incidents": 0, "documents": 0, "collection_total": index.vector_store.count()}
    return index.upsert_many(trusted)


def rebuild_trusted_index(store: Any, index: Any, *, model_key: str) -> dict[str, int]:
    """Rebuild the enriched locator so low-confidence/old-schema rows cannot be retrieved."""
    with store._connect() as conn:
        numbers = [
            str(row[0])
            for row in conn.execute(
                "SELECT incident_number FROM support_incident_knowledge ORDER BY incident_number"
            ).fetchall()
        ]
    items = [item for item in (store.get(number) for number in numbers) if item is not None]
    return index_trusted_items(index, items, model_key=model_key)


def rebuild_trusted_patterns(store: Any, *, model_key: str) -> int:
    """Materialise patterns using only trusted current-schema parent/action evidence."""
    threshold = float(settings.knowledge_min_extraction_confidence)
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM support_incident_knowledge
            WHERE COALESCE(pattern_key, '') <> ''
              AND model=?
              AND confidence>=?
            ORDER BY incident_number
            """,
            (model_key, threshold),
        ).fetchall()
        action_rows = conn.execute(
            "SELECT * FROM support_knowledge_actions ORDER BY incident_number, ordinal"
        ).fetchall()

    actions_by_incident: dict[str, list[Any]] = defaultdict(list)
    for row in action_rows:
        if safe_confidence(row["confidence"]) >= threshold:
            actions_by_incident[str(row["incident_number"])].append(row)

    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[str(row["pattern_key"])].append(row)

    payloads: list[tuple] = []
    for pattern_key, pattern_rows in grouped.items():
        label_counts = Counter(str(row["pattern_label"] or "") for row in pattern_rows)
        label = next((name for name, _ in label_counts.most_common() if name), pattern_key)
        incidents = {str(row["incident_number"]) for row in pattern_rows}
        confidences = [safe_confidence(row["confidence"]) for row in pattern_rows]

        resolutions: dict[str, set[str]] = defaultdict(set)
        resolution_labels: dict[str, str] = {}
        roots: dict[str, set[str]] = defaultdict(set)
        root_labels: dict[str, str] = {}
        technologies: dict[str, set[str]] = defaultdict(set)
        environments: dict[str, set[str]] = defaultdict(set)
        locations: dict[str, set[str]] = defaultdict(set)
        successful: dict[str, set[str]] = defaultdict(set)
        successful_labels: dict[str, str] = {}
        failed: dict[str, set[str]] = defaultdict(set)
        failed_labels: dict[str, str] = {}
        resolved_incidents: set[str] = set()

        for row in pattern_rows:
            number = str(row["incident_number"])
            raw_resolution = str(row["resolution_pattern"] or row["resolution"] or "").strip()
            if is_reusable_resolution(raw_resolution):
                resolution = canonical_resolution_family(raw_resolution)
                key = _key(resolution)
                if key:
                    resolutions[key].add(number)
                    resolution_labels.setdefault(key, resolution)
                    resolved_incidents.add(number)

            root = str(row["root_cause_pattern"] or row["root_cause"] or "").strip()
            if root:
                key = _key(root)
                roots[key].add(number)
                root_labels.setdefault(key, root)

            for technology in json.loads(row["technologies_json"] or "[]"):
                value = _space(technology)
                if value:
                    technologies[_key(value)].add(number)
            for environment in json.loads(row["environments_json"] or "[]"):
                value = _space(environment)
                if value:
                    environments[_key(value)].add(number)
            location = _space(row["location"])
            if location:
                locations[_key(location)].add(number)

            for action in actions_by_incident.get(number, []):
                raw_label = str(action["canonical_action"] or action["action"] or "").strip()
                outcome = str(action["outcome"] or "unknown").lower()
                if not raw_label:
                    continue
                label_value = canonical_action_family(raw_label)
                key = _key(label_value)
                if not key:
                    continue
                if outcome == "failed":
                    failed[key].add(number)
                    failed_labels.setdefault(key, label_value)
                elif outcome == "successful" and not is_passive_outcome(label_value):
                    successful[key].add(number)
                    successful_labels.setdefault(key, label_value)

        counter = store._counter_rows
        payloads.append(
            (
                pattern_key,
                label,
                len(incidents),
                len(resolved_incidents),
                sum(confidences) / len(confidences) if confidences else 0.0,
                json.dumps(counter(resolutions, labels=resolution_labels), ensure_ascii=False),
                json.dumps(counter(successful, labels=successful_labels), ensure_ascii=False),
                json.dumps(counter(failed, labels=failed_labels), ensure_ascii=False),
                json.dumps(counter(roots, labels=root_labels), ensure_ascii=False),
                json.dumps(counter(technologies), ensure_ascii=False),
                json.dumps(counter(environments), ensure_ascii=False),
                json.dumps(counter(locations), ensure_ascii=False),
                json.dumps(sorted(incidents), ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            )
        )

    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM support_patterns")
        conn.executemany(
            """
            INSERT INTO support_patterns(
                pattern_key,label,incident_count,resolved_count,avg_confidence,
                resolution_counts_json,successful_action_counts_json,
                failed_action_counts_json,root_cause_counts_json,technologies_json,
                environments_json,locations_json,incident_numbers_json,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            payloads,
        )
    return len(payloads)

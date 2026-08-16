"""Deterministic trust and canonicalisation rules for enriched support knowledge.

LLM extraction is evidence interpretation, not the final organisational truth.  These
helpers provide a narrow deterministic layer that decides what may contribute to
cross-incident rollups and collapses obvious wording variants into reusable families.
Exact incident evidence remains untouched and auditable.
"""

from __future__ import annotations

import re
from typing import Any


def _space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value: Any) -> str:
    text = _space(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def safe_confidence(value: Any, *, default: float = 0.0) -> float:
    """Return a bounded confidence value; malformed/null model output becomes untrusted."""
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def is_trusted_confidence(value: Any, *, threshold: float) -> bool:
    return safe_confidence(value) >= float(threshold)


# These are deliberately conservative.  They collapse wording variants only when the
# operational meaning is clear; we do not use semantic/LLM guesses in this layer.
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
    "service restored",
)


def canonical_resolution_family(value: Any) -> str:
    """Collapse only high-confidence lexical variants into a stable resolution family."""
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
    """Use the same narrow families for actions so counts align with resolutions."""
    return canonical_resolution_family(value)


def is_passive_outcome(value: Any) -> bool:
    key = _key(value)
    return bool(key) and any(_key(term) in key for term in _PASSIVE_OUTCOME_TERMS)


def is_generic_outcome(value: Any) -> bool:
    key = _key(value)
    return bool(key) and any(_key(term) in key for term in _GENERIC_OUTCOME_TERMS)


def is_reusable_resolution(value: Any) -> bool:
    """A reusable resolution must describe an intervention, not merely an observed outcome."""
    text = _space(value)
    if not text:
        return False
    return not is_passive_outcome(text) and not is_generic_outcome(text)

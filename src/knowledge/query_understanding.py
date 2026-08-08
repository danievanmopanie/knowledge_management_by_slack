"""Deterministic query normalization and IT identifier extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_IDENTIFIER_PATTERNS = {
    "incident_ids": re.compile(r"\bINC\d+\b", re.IGNORECASE),
    "change_ids": re.compile(r"\bCHG\d+\b", re.IGNORECASE),
    "problem_ids": re.compile(r"\bPRB\d+\b", re.IGNORECASE),
    "kb_ids": re.compile(r"\bKB\d+\b", re.IGNORECASE),
    "error_codes": re.compile(r"\b(?:0x[0-9A-F]+|[A-Z]{2,10}-?\d{2,})\b", re.IGNORECASE),
    "hostnames": re.compile(r"\b(?=[A-Z0-9-]{5,}\b)(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z0-9-]+\b", re.IGNORECASE),
}
_QUOTED = re.compile(r"[\"']([^\"']{2,80})[\"']")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class QueryHints:
    original: str
    normalized: str
    incident_ids: tuple[str, ...] = field(default_factory=tuple)
    change_ids: tuple[str, ...] = field(default_factory=tuple)
    problem_ids: tuple[str, ...] = field(default_factory=tuple)
    kb_ids: tuple[str, ...] = field(default_factory=tuple)
    error_codes: tuple[str, ...] = field(default_factory=tuple)
    hostnames: tuple[str, ...] = field(default_factory=tuple)
    quoted_phrases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def exact_terms(self) -> tuple[str, ...]:
        ordered = (
            *self.incident_ids,
            *self.change_ids,
            *self.problem_ids,
            *self.kb_ids,
            *self.error_codes,
            *self.hostnames,
            *self.quoted_phrases,
        )
        return tuple(dict.fromkeys(term for term in ordered if term))


def understand_query(text: str) -> QueryHints:
    original = text or ""
    normalized = _SPACE.sub(" ", original.strip())
    values: dict[str, tuple[str, ...]] = {}
    for name, pattern in _IDENTIFIER_PATTERNS.items():
        matches = [match.upper() for match in pattern.findall(original)]
        values[name] = tuple(dict.fromkeys(matches))
    quoted = tuple(dict.fromkeys(match.strip() for match in _QUOTED.findall(original) if match.strip()))
    return QueryHints(
        original=original,
        normalized=normalized,
        quoted_phrases=quoted,
        **values,
    )

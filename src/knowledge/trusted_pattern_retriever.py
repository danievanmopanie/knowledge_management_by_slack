"""Bridge semantic symptom retrieval to authoritative trusted pattern rollups.

Embeddings are deliberately used only to choose the most likely canonical issue
pattern. Once a pattern wins, every organisational count and reusable claim comes
from the materialised ``support_patterns`` row built by the deterministic trust
layer. Raw extraction wording from neighbouring incidents is never re-aggregated
into organisational truth here.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from src.core.config import settings
from src.knowledge.organisational_knowledge import (
    EnrichedIncidentKnowledge,
    OrganisationalKnowledgeRetriever,
)


@dataclass(frozen=True)
class TrustedPatternMatch:
    pattern: dict[str, Any]
    candidate_hits: tuple[tuple[EnrichedIncidentKnowledge, float], ...]
    cohesion: float
    mean_similarity: float
    evidence_strength: str


class TrustedPatternKnowledgeRetriever(OrganisationalKnowledgeRetriever):
    """Locate a pattern semantically, then answer from its trusted rollup only."""

    PATTERN_SCORE_WINDOW = 0.08
    PATTERN_VOTE_LIMIT = 12

    @staticmethod
    def _pattern_evidence_strength(
        *,
        incident_count: int,
        candidate_hits: int,
        mean_similarity: float,
        avg_confidence: float,
        cohesion: float,
    ) -> str:
        if (
            incident_count >= 5
            and candidate_hits >= 3
            and mean_similarity >= 0.60
            and avg_confidence >= 0.80
            and cohesion >= 0.60
        ):
            return "strong"
        if (
            incident_count >= 2
            and candidate_hits >= 2
            and mean_similarity >= 0.50
            and avg_confidence >= settings.knowledge_min_extraction_confidence
            and cohesion >= 0.40
        ):
            return "moderate"
        return "limited"

    @staticmethod
    def _family_for_incident(pattern: dict[str, Any], incident_number: str) -> str:
        for row in pattern.get("resolution_counts") or []:
            if incident_number in (row.get("incidents") or []):
                return str(row.get("label") or "").strip()
        return ""

    def _select_dominant_pattern(
        self,
        selected: list[tuple[EnrichedIncidentKnowledge, float]],
    ) -> tuple[str, list[tuple[EnrichedIncidentKnowledge, float]], float]:
        if not selected:
            return "", [], 0.0

        top_score = selected[0][1]
        floor = max(
            float(settings.knowledge_min_similarity),
            top_score - self.PATTERN_SCORE_WINDOW,
        )
        vote_pool = [pair for pair in selected if pair[1] >= floor][: self.PATTERN_VOTE_LIMIT]
        if not vote_pool:
            return "", [], 0.0

        weighted: dict[str, float] = defaultdict(float)
        hits: dict[str, int] = defaultdict(int)
        for item, score in vote_pool:
            if not item.pattern_key:
                continue
            weighted[item.pattern_key] += score
            hits[item.pattern_key] += 1

        if not weighted:
            return "", [], 0.0

        winner = max(weighted, key=lambda key: (weighted[key], hits[key], key))
        winner_hits = [pair for pair in vote_pool if pair[0].pattern_key == winner]
        cohesion = len(winner_hits) / len(vote_pool)
        return winner, winner_hits, cohesion

    def resolve_pattern(
        self,
        query: str,
        *,
        candidate_k: int = 40,
        max_incidents: int = 24,
        min_similarity: float | None = None,
    ) -> TrustedPatternMatch | None:
        query = " ".join(str(query or "").split())
        if not query:
            return None

        threshold = (
            float(settings.knowledge_min_similarity)
            if min_similarity is None
            else float(min_similarity)
        )
        try:
            docs = self.index.vector_store.similarity_search(
                query,
                k=max(1, int(candidate_k)),
                where={"knowledge_kind": "symptom"},
            )
        except Exception:
            return None

        best_scores: dict[str, float] = {}
        for doc in docs:
            meta = doc.metadata or {}
            number = str(meta.get("number") or "").upper().strip()
            score = float(meta.get("score") or 0.0)
            if not number or score < threshold:
                continue
            best_scores[number] = max(score, best_scores.get(number, -1.0))

        ranked = sorted(best_scores.items(), key=lambda item: item[1], reverse=True)
        selected: list[tuple[EnrichedIncidentKnowledge, float]] = []
        for number, score in ranked[: max(1, int(max_incidents))]:
            item = self.store.get(number)
            if item is None:
                continue
            if item.confidence < float(settings.knowledge_min_extraction_confidence):
                continue
            selected.append((item, score))
        if not selected:
            return None

        pattern_key, candidate_hits, cohesion = self._select_dominant_pattern(selected)
        if not pattern_key or not candidate_hits:
            return None

        pattern = self.store.get_pattern(pattern_key)
        if pattern is None:
            return None

        mean_similarity = sum(score for _, score in candidate_hits) / len(candidate_hits)
        quality = self._pattern_evidence_strength(
            incident_count=int(pattern.get("incident_count") or 0),
            candidate_hits=len(candidate_hits),
            mean_similarity=mean_similarity,
            avg_confidence=float(pattern.get("avg_confidence") or 0.0),
            cohesion=cohesion,
        )
        return TrustedPatternMatch(
            pattern=pattern,
            candidate_hits=tuple(candidate_hits),
            cohesion=cohesion,
            mean_similarity=mean_similarity,
            evidence_strength=quality,
        )

    def _render_context(self, match: TrustedPatternMatch, *, max_chars: int = 9000) -> str:
        pattern = match.pattern
        supporting = list(pattern.get("incident_numbers") or [])
        lines = [
            "### Organisational pattern evidence",
            "Semantic retrieval selected the canonical pattern; counts below come only from the trusted materialised pattern store.",
            "Preserve small-sample frequencies as x of y incidents; do not convert them into percentages or probabilities.",
            f"Canonical issue pattern: {pattern.get('label') or pattern.get('pattern_key')}",
            f"Trusted supporting incidents: {int(pattern.get('incident_count') or 0)}",
            f"Incidents with reusable resolution knowledge: {int(pattern.get('resolved_count') or 0)}",
            f"High-relevance candidate hits for this pattern: {len(match.candidate_hits)}",
            f"Mean similarity of those hits: {match.mean_similarity:.2f}",
            f"Pattern cohesion in the high-relevance neighbourhood: {match.cohesion:.2f}",
            f"Mean trusted extraction confidence: {float(pattern.get('avg_confidence') or 0.0):.2f}",
            f"Evidence strength: {match.evidence_strength}",
        ]
        if supporting:
            lines.append("Supporting incidents: " + ", ".join(supporting[:20]))

        lines.extend(self._render_counter("Trusted resolution families:", pattern.get("resolution_counts") or []))
        lines.extend(self._render_counter("Trusted successful actions:", pattern.get("successful_action_counts") or []))
        lines.extend(self._render_counter("Trusted failed actions / no-fix paths:", pattern.get("failed_action_counts") or []))
        lines.extend(self._render_counter("Observed trusted root causes:", pattern.get("root_cause_counts") or []))
        if not (pattern.get("root_cause_counts") or []):
            lines.append("Observed trusted root causes: none repeated in the current evidence.")
        if not (pattern.get("failed_action_counts") or []):
            lines.append("Trusted failed actions / no-fix paths: none repeated in the current evidence.")

        lines.extend(["", "#### Closest trusted cases in the selected pattern"])
        for item, score in match.candidate_hits[:5]:
            family = self._family_for_incident(pattern, item.incident_number)
            lines.append(
                f"- {item.incident_number} (similarity {score:.2f}) — "
                f"symptom: {item.symptom or item.pattern_label or '[not captured]'}; "
                f"trusted resolution family: {family or '[no reusable resolution recorded]'}"
            )

        text = "\n".join(lines).strip()
        return text if len(text) <= max_chars else text[:max_chars].rstrip() + "\n[Organisational pattern context truncated]"

    @staticmethod
    def _incident_refs(row: dict[str, Any]) -> str:
        incidents = [str(value) for value in (row.get("incidents") or []) if str(value).strip()]
        return ", ".join(incidents)

    def _render_deterministic_response(self, match: TrustedPatternMatch) -> str:
        pattern = match.pattern
        label = str(pattern.get("label") or pattern.get("pattern_key") or "the selected issue pattern")
        incident_count = int(pattern.get("incident_count") or 0)
        resolutions = list(pattern.get("resolution_counts") or [])
        failed = list(pattern.get("failed_action_counts") or [])
        roots = list(pattern.get("root_cause_counts") or [])

        lines = [
            f"We have *{incident_count} trusted incidents* matching *{label}*.",
            "",
            "*What has worked before*",
        ]
        if resolutions:
            for row in resolutions[:4]:
                refs = self._incident_refs(row)
                suffix = f" — {refs}" if refs else ""
                lines.append(
                    f"• *{row.get('label')}* — {int(row.get('count') or 0)} of {incident_count} trusted incidents{suffix}"
                )
        else:
            lines.append("• No reusable resolution has been established for this pattern yet.")

        if resolutions:
            top = resolutions[0]
            second_count = int(resolutions[1].get("count") or 0) if len(resolutions) > 1 else -1
            top_count = int(top.get("count") or 0)
            lines.extend(["", "*Best-supported next step*"])
            if top_count > second_count:
                lines.append(
                    f"First verify whether the current incident is consistent with the leading recorded path: "
                    f"*{top.get('label')}*. It appears in {top_count} of {incident_count} trusted incidents. "
                    "Do not assume it applies until the current incident evidence supports that path."
                )
            else:
                lines.append(
                    "There is no single leading resolution path in the current trusted evidence. "
                    "Use the recorded paths above as evidence-backed branches rather than assuming one applies."
                )

        lines.extend(["", "*Root-cause evidence*"])
        if roots:
            repeated = [row for row in roots if int(row.get("count") or 0) > 1]
            if repeated:
                for row in repeated[:4]:
                    refs = self._incident_refs(row)
                    suffix = f" — {refs}" if refs else ""
                    lines.append(f"• {row.get('label')} — {int(row.get('count') or 0)} incidents{suffix}")
            else:
                labels = "; ".join(str(row.get("label")) for row in roots[:4] if row.get("label"))
                lines.append(
                    "Individual cases record different root causes"
                    + (f" ({labels})" if labels else "")
                    + ", so the current evidence does not support one universal root cause."
                )
        else:
            lines.append("No repeated root cause has been established in the trusted evidence.")

        lines.extend(["", "*Failed paths*"])
        if failed:
            for row in failed[:4]:
                refs = self._incident_refs(row)
                suffix = f" — {refs}" if refs else ""
                lines.append(f"• {row.get('label')} — {int(row.get('count') or 0)} incident(s){suffix}")
        else:
            lines.append("No repeated failed troubleshooting path has been established for this pattern.")

        return "\n".join(lines).strip()

    def collective_bundle(
        self,
        query: str,
        *,
        candidate_k: int = 40,
        max_incidents: int = 24,
        min_similarity: float | None = None,
        max_chars: int = 9000,
    ) -> tuple[str, str]:
        match = self.resolve_pattern(
            query,
            candidate_k=candidate_k,
            max_incidents=max_incidents,
            min_similarity=min_similarity,
        )
        if match is None:
            return "", ""
        return self._render_context(match, max_chars=max_chars), self._render_deterministic_response(match)

    def collective_context(
        self,
        query: str,
        *,
        candidate_k: int = 40,
        max_incidents: int = 24,
        min_similarity: float | None = None,
        max_chars: int = 9000,
    ) -> str:
        context, _response = self.collective_bundle(
            query,
            candidate_k=candidate_k,
            max_incidents=max_incidents,
            min_similarity=min_similarity,
            max_chars=max_chars,
        )
        return context

    def deterministic_response(
        self,
        query: str,
        *,
        candidate_k: int = 40,
        max_incidents: int = 24,
        min_similarity: float | None = None,
    ) -> str:
        _context, response = self.collective_bundle(
            query,
            candidate_k=candidate_k,
            max_incidents=max_incidents,
            min_similarity=min_similarity,
        )
        return response

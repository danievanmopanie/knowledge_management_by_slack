"""Bridge semantic symptom retrieval to authoritative trusted pattern rollups.

Embeddings are deliberately used only to choose the most likely canonical issue
pattern. Once a pattern wins, every organisational count and reusable claim comes
from the materialised ``support_patterns`` row built by the deterministic trust
layer. Raw extraction wording from neighbouring incidents is never re-aggregated
into organisational truth here.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.core.config import settings
from src.knowledge.organisational_knowledge import (
    EnrichedIncidentKnowledge,
    OrganisationalKnowledgeRetriever,
)


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
        """Rate evidence using relevance, support, trust and neighbourhood cohesion."""
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
        """Return the trusted normalised resolution family supporting one incident."""
        for row in pattern.get("resolution_counts") or []:
            if incident_number in (row.get("incidents") or []):
                return str(row.get("label") or "").strip()
        return ""

    def _select_dominant_pattern(
        self,
        selected: list[tuple[EnrichedIncidentKnowledge, float]],
    ) -> tuple[str, list[tuple[EnrichedIncidentKnowledge, float]], float]:
        """Pick one canonical pattern from a tight high-similarity neighbourhood."""
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
            # Similarity drives pattern selection. A small hit bonus makes a
            # repeated coherent pattern beat a one-off neighbour at similar score.
            weighted[item.pattern_key] += score
            hits[item.pattern_key] += 1

        if not weighted:
            return "", [], 0.0

        winner = max(
            weighted,
            key=lambda key: (weighted[key], hits[key], key),
        )
        winner_hits = [pair for pair in vote_pool if pair[0].pattern_key == winner]
        cohesion = len(winner_hits) / len(vote_pool)
        return winner, winner_hits, cohesion

    def collective_context(
        self,
        query: str,
        *,
        candidate_k: int = 40,
        max_incidents: int = 24,
        min_similarity: float | None = None,
        max_chars: int = 9000,
    ) -> str:
        """Resolve a symptom query to one trusted materialised organisational pattern."""
        query = " ".join(str(query or "").split())
        if not query:
            return ""

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
            return ""

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
            # The trusted vector index should already enforce this, but re-check
            # parent confidence at the serving boundary as defence in depth.
            if item.confidence < float(settings.knowledge_min_extraction_confidence):
                continue
            selected.append((item, score))
        if not selected:
            return ""

        pattern_key, candidate_hits, cohesion = self._select_dominant_pattern(selected)
        if not pattern_key or not candidate_hits:
            return ""

        pattern = self.store.get_pattern(pattern_key)
        if pattern is None:
            return ""

        incident_count = int(pattern.get("incident_count") or 0)
        avg_confidence = float(pattern.get("avg_confidence") or 0.0)
        mean_similarity = sum(score for _, score in candidate_hits) / len(candidate_hits)
        quality = self._pattern_evidence_strength(
            incident_count=incident_count,
            candidate_hits=len(candidate_hits),
            mean_similarity=mean_similarity,
            avg_confidence=avg_confidence,
            cohesion=cohesion,
        )
        supporting = list(pattern.get("incident_numbers") or [])

        lines = [
            "### Organisational pattern evidence",
            "Semantic retrieval selected the canonical pattern; counts below come only from the trusted materialised pattern store.",
            "When describing frequency to a technician, preserve these small-sample counts as 'x of y incidents'; do not convert them into percentages or probabilities.",
            f"Canonical issue pattern: {pattern.get('label') or pattern_key}",
            f"Trusted supporting incidents: {incident_count}",
            f"Incidents with reusable resolution knowledge: {int(pattern.get('resolved_count') or 0)}",
            f"High-relevance candidate hits for this pattern: {len(candidate_hits)}",
            f"Mean similarity of those hits: {mean_similarity:.2f}",
            f"Pattern cohesion in the high-relevance neighbourhood: {cohesion:.2f}",
            f"Mean trusted extraction confidence: {avg_confidence:.2f}",
            f"Evidence strength: {quality}",
        ]
        if supporting:
            lines.append("Supporting incidents: " + ", ".join(supporting[:20]))

        lines.extend(
            self._render_counter(
                "Trusted resolution families:",
                pattern.get("resolution_counts") or [],
            )
        )
        lines.extend(
            self._render_counter(
                "Trusted successful actions:",
                pattern.get("successful_action_counts") or [],
            )
        )
        lines.extend(
            self._render_counter(
                "Trusted failed actions / no-fix paths:",
                pattern.get("failed_action_counts") or [],
            )
        )
        lines.extend(
            self._render_counter(
                "Observed trusted root causes:",
                pattern.get("root_cause_counts") or [],
            )
        )
        if not (pattern.get("root_cause_counts") or []):
            lines.append("Observed trusted root causes: none repeated in the current evidence.")
        if not (pattern.get("failed_action_counts") or []):
            lines.append("Trusted failed actions / no-fix paths: none repeated in the current evidence.")

        lines.extend(["", "#### Closest trusted cases in the selected pattern"])
        for item, score in candidate_hits[:5]:
            family = self._family_for_incident(pattern, item.incident_number)
            resolution_text = family or "[no reusable resolution recorded]"
            lines.append(
                f"- {item.incident_number} (similarity {score:.2f}) — "
                f"symptom: {item.symptom or item.pattern_label or '[not captured]'}; "
                f"trusted resolution family: {resolution_text}"
            )

        text = "\n".join(lines).strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + "\n[Organisational pattern context truncated]"

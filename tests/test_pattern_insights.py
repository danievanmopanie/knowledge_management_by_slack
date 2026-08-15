from types import SimpleNamespace

from langchain_core.documents import Document

from src.knowledge.pattern_insights import organisation_wide_pattern_context


class _VectorStore:
    def similarity_search(self, query, k=5, where=None):
        assert where == {"knowledge_kind": "symptom"}
        return [
            Document(page_content="", metadata={"number": "INC001", "score": 0.93}),
            Document(page_content="", metadata={"number": "INC002", "score": 0.90}),
            Document(page_content="", metadata={"number": "INC003", "score": 0.88}),
        ]


class _Store:
    def get(self, number):
        if number in {"INC001", "INC002"}:
            return SimpleNamespace(
                pattern_key="outlook-credential-prompt",
                pattern_label="Microsoft Outlook repeated credential prompt",
            )
        return SimpleNamespace(
            pattern_key="vpn-authentication-failure",
            pattern_label="VPN authentication failure",
        )

    def get_pattern(self, key):
        if key != "outlook-credential-prompt":
            raise AssertionError("single-hit unrelated pattern must not be expanded")
        return {
            "label": "Microsoft Outlook repeated credential prompt",
            "incident_count": 63,
            "resolved_count": 51,
            "avg_confidence": 0.86,
            "resolution_counts": [
                {
                    "label": "Clear cached Office credentials",
                    "count": 42,
                    "incidents": ["INC001", "INC002", "INC010"],
                }
            ],
            "successful_action_counts": [],
            "failed_action_counts": [
                {
                    "label": "Reset password only",
                    "count": 19,
                    "incidents": ["INC001", "INC002"],
                }
            ],
            "root_cause_counts": [],
        }


class _Retriever:
    index = SimpleNamespace(vector_store=_VectorStore())
    store = _Store()


def test_semantic_neighbourhood_expands_to_complete_organisation_wide_pattern():
    text = organisation_wide_pattern_context(
        _Retriever(),
        "Outlook keeps asking for password after reset",
        min_similarity=0.4,
        min_candidate_support=2,
    )

    assert "Organisation-wide reusable knowledge" in text
    assert "2 semantically matching incident(s)" in text
    assert "Total enriched incidents in this canonical pattern: 63" in text
    assert "Incidents with recorded resolution knowledge: 51" in text
    assert "Clear cached Office credentials: 42 incident(s)" in text
    assert "Reset password only: 19 incident(s)" in text
    assert "VPN authentication failure" not in text

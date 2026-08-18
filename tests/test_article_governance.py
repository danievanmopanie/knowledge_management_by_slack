from src.knowledge.article_governance import ArticleGovernanceStore


def test_owner_assignment_is_replaceable_and_auditable(tmp_path):
    store = ArticleGovernanceStore(tmp_path / "platform.db")

    first = store.assign_owner(
        document_id="doc_1",
        owner_user_id="U_OWNER_1",
        assigned_by_user_id="U_ADMIN",
    )
    assert first["owner_user_id"] == "U_OWNER_1"
    assert first["assigned_by_user_id"] == "U_ADMIN"

    second = store.assign_owner(
        document_id="doc_1",
        owner_user_id="U_OWNER_2",
        assigned_by_user_id="U_OWNER_1",
    )
    assert second["owner_user_id"] == "U_OWNER_2"
    assert second["assigned_by_user_id"] == "U_OWNER_1"


def test_owner_can_request_review_from_another_user_or_self(tmp_path):
    store = ArticleGovernanceStore(tmp_path / "platform.db")

    external = store.request_review(
        document_id="doc_1",
        version_id="v1",
        reviewer_user_id="U_REVIEWER",
        requested_by_user_id="U_OWNER",
        review_note="Validate the Windows Audio steps",
    )
    self_review = store.request_review(
        document_id="doc_1",
        version_id="v1",
        reviewer_user_id="U_OWNER",
        requested_by_user_id="U_OWNER",
        review_note="Final owner review",
    )

    assert external["reviewer_user_id"] == "U_REVIEWER"
    assert external["review_note"] == "Validate the Windows Audio steps"
    assert self_review["reviewer_user_id"] == "U_OWNER"
    assert len(store.pending_reviews_for("U_OWNER")) == 1


def test_completed_review_leaves_pending_queue_and_keeps_input(tmp_path):
    store = ArticleGovernanceStore(tmp_path / "platform.db")
    review = store.request_review(
        document_id="doc_1",
        version_id="v2",
        reviewer_user_id="U_REVIEWER",
        requested_by_user_id="U_OWNER",
        shared_channel_id="C_KNOWLEDGE",
        shared_message_ts="123.456",
    )

    completed = store.complete_review(
        review["id"],
        reviewer_user_id="U_REVIEWER",
        response_note="The restart step is correct, but run it after driver installation.",
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["completed_at"]
    assert "after driver installation" in completed["response_note"]
    assert completed["shared_channel_id"] == "C_KNOWLEDGE"
    assert completed["shared_message_ts"] == "123.456"
    assert store.pending_reviews_for("U_REVIEWER") == []
    assert store.completed_reviews_for_article("doc_1", version_id="v2")[0]["id"] == review["id"]


def test_review_cannot_be_completed_by_someone_else(tmp_path):
    store = ArticleGovernanceStore(tmp_path / "platform.db")
    review = store.request_review(
        document_id="doc_1",
        version_id="v2",
        reviewer_user_id="U_REVIEWER",
        requested_by_user_id="U_OWNER",
    )

    completed = store.complete_review(
        review["id"],
        reviewer_user_id="U_OTHER",
        response_note="I should not be able to submit this.",
    )

    assert completed is None
    assert store.get_review(review["id"])["status"] == "requested"

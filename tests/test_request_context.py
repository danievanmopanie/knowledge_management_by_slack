"""Regression tests for request context and safe user-facing errors."""

from src.core.context import RequestContext
from src.core.errors import safe_error_message


def test_request_context_from_slack_is_typed_and_correlated():
    context = RequestContext.from_slack(
        channel_id="C123",
        user_id="U123",
        thread_ts="123.456",
        files=[{"id": "F1", "name": "runbook.md"}],
    )

    assert context.channel_id == "C123"
    assert context.user_id == "U123"
    assert context.thread_ts == "123.456"
    assert context.files[0]["id"] == "F1"
    assert len(context.request_id) == 12


def test_safe_error_contains_reference_but_not_exception_details():
    message = safe_error_message("abc123")

    assert "abc123" in message
    assert "Traceback" not in message
    assert "Exception" not in message

"""Safe user-facing error helpers."""


def safe_error_message(request_id: str) -> str:
    """Return a Slack-safe error without exposing internal exception details."""
    return (
        "Sorry, I could not complete that request. "
        f"Please quote reference `{request_id}` if the problem continues."
    )

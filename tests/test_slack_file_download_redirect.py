from pathlib import Path

import pytest

from src.knowledge import file_loader


class FakeResponse:
    def __init__(self, *, status_code, url, headers=None, body=b""):
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self):
        yield self._body


class FakeClient:
    responses = []
    requests = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, headers=None):
        self.requests.append((method, url, dict(headers or {})))
        return self.responses.pop(0)


@pytest.mark.anyio
async def test_slack_cross_host_redirect_preserves_bot_auth(monkeypatch, tmp_path):
    first = "https://files.slack.com/files-pri/T123-F123/download/incident.csv"
    second = "https://customer-research-hq.slack.com/files-pri/T123-F123/download/incident.csv"
    FakeClient.requests = []
    FakeClient.responses = [
        FakeResponse(status_code=302, url=first, headers={"location": second}),
        FakeResponse(status_code=200, url=second, body=b"number,short_description\nINC1,test\n"),
    ]
    monkeypatch.setattr(file_loader.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(file_loader.settings, "slack_bot_token", "xoxb-test")

    path = await file_loader.download_slack_file(
        {
            "id": "F123",
            "name": "incident.csv",
            "size": 40,
            "url_private_download": first,
        },
        target_dir=tmp_path,
    )

    assert path.exists()
    assert path.read_bytes().startswith(b"number,short_description")
    assert [request[2]["Authorization"] for request in FakeClient.requests] == [
        "Bearer xoxb-test",
        "Bearer xoxb-test",
    ]


@pytest.mark.anyio
async def test_slack_redirect_to_untrusted_host_is_rejected(monkeypatch, tmp_path):
    first = "https://files.slack.com/files-pri/T123-F123/download/incident.csv"
    FakeClient.requests = []
    FakeClient.responses = [
        FakeResponse(
            status_code=302,
            url=first,
            headers={"location": "https://example.com/stolen.csv"},
        )
    ]
    monkeypatch.setattr(file_loader.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(file_loader.settings, "slack_bot_token", "xoxb-test")

    with pytest.raises(file_loader.UploadValidationError, match="trusted Slack host boundary"):
        await file_loader.download_slack_file(
            {
                "id": "F123",
                "name": "incident.csv",
                "size": 40,
                "url_private_download": first,
            },
            target_dir=Path(tmp_path),
        )

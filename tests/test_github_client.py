"""Tests for the minimal GitHub REST client used by the Builder Agent worker."""

import json

import httpx
import pytest

from src.core.config import settings
from src.integrations.github_client import GitHubClientError, create_pull_request


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "ghp_test")
    monkeypatch.setattr(settings, "github_repo_owner", "acme")
    monkeypatch.setattr(settings, "github_repo_name", "widgets")
    monkeypatch.setattr(settings, "builder_base_branch", "main")
    monkeypatch.setattr(settings, "github_api_base_url", "https://api.github.com")


_RealClient = httpx.Client


def _mock_client(handler):
    return lambda *args, **kwargs: _RealClient(
        transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")
    )


def test_create_pull_request_success(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201, json={"html_url": "https://github.com/acme/widgets/pull/7", "number": 7}
        )

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))

    result = create_pull_request(
        branch_name="builder/bld_1",
        title="Builder Agent: do a thing",
        body="details",
    )

    assert result == {"html_url": "https://github.com/acme/widgets/pull/7", "number": "7"}
    assert captured["url"] == "https://api.github.com/repos/acme/widgets/pulls"
    assert captured["headers"]["authorization"] == "Bearer ghp_test"
    assert captured["body"] == {
        "title": "Builder Agent: do a thing",
        "head": "builder/bld_1",
        "base": "main",
        "body": "details",
    }


def test_create_pull_request_raises_on_error_response(monkeypatch):
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="Validation Failed")

    monkeypatch.setattr(httpx, "Client", _mock_client(handler))

    with pytest.raises(GitHubClientError):
        create_pull_request(branch_name="builder/bld_1", title="t", body="b")


def test_create_pull_request_requires_token(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "github_token", "")

    with pytest.raises(GitHubClientError):
        create_pull_request(branch_name="builder/bld_1", title="t", body="b")


def test_create_pull_request_requires_repo_config(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "github_repo_owner", "")

    with pytest.raises(GitHubClientError):
        create_pull_request(branch_name="builder/bld_1", title="t", body="b")

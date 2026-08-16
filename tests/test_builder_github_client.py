"""Tests for GitHub PR handoff resolution used by the Builder worker."""

import pytest

from src.core.config import settings
from src.integrations.github_client import (
    GitHubClientError,
    get_pull_request,
    merge_pull_request,
    pr_number_from_url,
)


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response, **_kwargs):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, _url, headers):
        assert headers["Authorization"] == "Bearer test-token"
        return self.response


def _settings(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "test-token")
    monkeypatch.setattr(settings, "github_repo_owner", "danievanmopanie")
    monkeypatch.setattr(settings, "github_repo_name", "knowledge_management_by_slack")
    monkeypatch.setattr(settings, "github_api_base_url", "https://api.github.test")


def _payload(head_repo="danievanmopanie/knowledge_management_by_slack"):
    return {
        "number": 83,
        "html_url": "https://github.com/danievanmopanie/knowledge_management_by_slack/pull/83",
        "title": "Improve Builder",
        "body": "Do the thing",
        "state": "open",
        "merged": False,
        "head": {
            "ref": "feature/from-chatgpt",
            "sha": "abc123",
            "repo": {"full_name": head_repo},
        },
        "base": {"ref": "main"},
    }


def test_get_pull_request_resolves_same_repo_head_branch(monkeypatch):
    _settings(monkeypatch)
    fake = _FakeClient(_FakeResponse(_payload()))
    monkeypatch.setattr("src.integrations.github_client.httpx.Client", lambda **kwargs: fake)

    pr = get_pull_request("83")

    assert pr["number"] == "83"
    assert pr["head_ref"] == "feature/from-chatgpt"
    assert pr["base_ref"] == "main"
    assert pr["html_url"].endswith("/pull/83")


def test_get_pull_request_rejects_closed_pr(monkeypatch):
    _settings(monkeypatch)
    payload = _payload()
    payload["state"] = "closed"
    fake = _FakeClient(_FakeResponse(payload))
    monkeypatch.setattr("src.integrations.github_client.httpx.Client", lambda **kwargs: fake)

    with pytest.raises(GitHubClientError, match="not open"):
        get_pull_request(83)


def test_get_pull_request_rejects_fork_branch_for_mutation(monkeypatch):
    _settings(monkeypatch)
    fake = _FakeClient(_FakeResponse(_payload("someone-else/fork")))
    monkeypatch.setattr("src.integrations.github_client.httpx.Client", lambda **kwargs: fake)

    with pytest.raises(GitHubClientError, match="fork"):
        get_pull_request(83)


def test_pr_number_from_url_extracts_number():
    assert pr_number_from_url("https://github.com/org/repo/pull/83") == "83"
    assert pr_number_from_url("https://github.com/org/repo/pull/83/files") == "83"
    assert pr_number_from_url("not a pr url") is None


class _FakePutClient:
    def __init__(self, response, **_kwargs):
        self.response = response
        self.put_calls: list[tuple[str, dict, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def put(self, url, headers, json):
        self.put_calls.append((url, headers, json))
        return self.response


def test_merge_pull_request_success(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(settings, "builder_merge_method", "squash")
    fake = _FakePutClient(_FakeResponse({"merged": True, "sha": "def456", "message": "Merged"}))
    monkeypatch.setattr("src.integrations.github_client.httpx.Client", lambda **kwargs: fake)

    result = merge_pull_request("83", sha="abc123")

    assert result == {"merged": True, "sha": "def456", "message": "Merged"}
    url, headers, payload = fake.put_calls[0]
    assert url.endswith("/pulls/83/merge")
    assert headers["Authorization"] == "Bearer test-token"
    assert payload == {"merge_method": "squash", "sha": "abc123"}


def test_merge_pull_request_not_mergeable_raises_405(monkeypatch):
    _settings(monkeypatch)
    fake = _FakePutClient(_FakeResponse({"message": "Not mergeable"}, status_code=405))
    monkeypatch.setattr("src.integrations.github_client.httpx.Client", lambda **kwargs: fake)

    with pytest.raises(GitHubClientError, match="not mergeable"):
        merge_pull_request("83", sha="abc123")


def test_merge_pull_request_stale_sha_raises_409(monkeypatch):
    _settings(monkeypatch)
    fake = _FakePutClient(_FakeResponse({"message": "Head branch was modified"}, status_code=409))
    monkeypatch.setattr("src.integrations.github_client.httpx.Client", lambda **kwargs: fake)

    with pytest.raises(GitHubClientError, match="moved since"):
        merge_pull_request("83", sha="stale-sha")

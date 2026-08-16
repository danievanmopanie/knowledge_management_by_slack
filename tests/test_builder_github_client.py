"""Tests for GitHub PR handoff resolution used by the Builder worker."""

import pytest

from src.core.config import settings
from src.integrations.github_client import GitHubClientError, get_pull_request


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

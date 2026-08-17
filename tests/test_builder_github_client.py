"""Tests for GitHub PR handoff resolution used by the Builder worker."""

import pytest

from src.core.config import settings
from src.integrations.github_client import GitHubClientError, get_pull_request, list_pull_requests


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

    def get(self, _url, headers, params=None):
        assert headers["Authorization"] == "Bearer test-token"
        return self.response


def _settings(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "test-token")
    monkeypatch.setattr(settings, "github_repo_owner", "danievanmopanie")
    monkeypatch.setattr(settings, "github_repo_name", "knowledge_management_by_slack")
    monkeypatch.setattr(settings, "github_api_base_url", "https://api.github.test")


def _payload(number=83, head_repo="danievanmopanie/knowledge_management_by_slack"):
    return {
        "number": number,
        "html_url": f"https://github.com/danievanmopanie/knowledge_management_by_slack/pull/{number}",
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


def test_list_pull_requests_exposes_exact_result_count(monkeypatch):
    _settings(monkeypatch)
    fake = _FakeClient(_FakeResponse([_payload(79), _payload(76), _payload(74)]))
    monkeypatch.setattr("src.integrations.github_client.httpx.Client", lambda **kwargs: fake)

    prs = list_pull_requests(state="open", limit=20)

    assert len(prs) == 3
    assert {pr["list_result_count"] for pr in prs} == {3}


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
    fake = _FakeClient(_FakeResponse(_payload(head_repo="someone-else/fork")))
    monkeypatch.setattr("src.integrations.github_client.httpx.Client", lambda **kwargs: fake)

    with pytest.raises(GitHubClientError, match="fork"):
        get_pull_request(83)

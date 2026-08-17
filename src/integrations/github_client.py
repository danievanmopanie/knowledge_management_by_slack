"""Minimal GitHub REST client for Builder Agent pull-request operations."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)
_PR_NUMBER_RE = re.compile(r"/pull/(\d+)(?:/|$)")


class GitHubClientError(Exception):
    """Raised when a Builder GitHub operation fails."""


def _headers() -> dict[str, str]:
    if not settings.github_token:
        raise GitHubClientError("GITHUB_TOKEN is not configured.")
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _owner_repo(owner: str | None = None, repo: str | None = None) -> tuple[str, str]:
    owner = owner or settings.github_repo_owner
    repo = repo or settings.github_repo_name
    if not owner or not repo:
        raise GitHubClientError("GITHUB_REPO_OWNER/GITHUB_REPO_NAME are not configured.")
    return owner, repo


def create_pull_request(
    *,
    branch_name: str,
    title: str,
    body: str,
    base_branch: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, str]:
    """Open a PR from branch_name into base_branch via the GitHub REST API."""
    owner, repo = _owner_repo(owner, repo)
    base = base_branch or settings.builder_base_branch
    url = f"{settings.github_api_base_url}/repos/{owner}/{repo}/pulls"
    payload = {"title": title, "head": branch_name, "base": base, "body": body}

    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, headers=_headers(), json=payload)

    if response.status_code >= 400:
        logger.error(
            "GitHub PR creation failed status=%s body=%s", response.status_code, response.text
        )
        raise GitHubClientError(f"GitHub API error {response.status_code}: {response.text[:500]}")

    data = response.json()
    return {"html_url": data["html_url"], "number": str(data["number"])}


def _normalize_pull_request(data: dict[str, Any]) -> dict[str, Any]:
    head = data.get("head") or {}
    base = data.get("base") or {}
    return {
        "number": str(data.get("number") or ""),
        "html_url": data.get("html_url") or "",
        "title": data.get("title") or "",
        "body": data.get("body") or "",
        "state": data.get("state") or "",
        "draft": bool(data.get("draft")),
        "merged": bool(data.get("merged")),
        "mergeable": data.get("mergeable"),
        "head_ref": head.get("ref") or "",
        "head_sha": head.get("sha") or "",
        "base_ref": base.get("ref") or "",
        "base_sha": base.get("sha") or "",
        "head_repo_full_name": (head.get("repo") or {}).get("full_name") or "",
    }


def list_pull_requests(
    *,
    state: str = "open",
    limit: int = 20,
    owner: str | None = None,
    repo: str | None = None,
) -> list[dict[str, Any]]:
    """List pull requests in the configured repository for Builder inspection.

    This runs in the trusted worker process, not the scrubbed child shell, so
    GitHub credentials remain unavailable to model-generated shell commands.
    """
    if state not in {"open", "closed", "all"}:
        raise GitHubClientError(f"Invalid pull request state: {state!r}")
    owner, repo = _owner_repo(owner, repo)
    bounded_limit = max(1, min(int(limit), 50))
    url = f"{settings.github_api_base_url}/repos/{owner}/{repo}/pulls"
    params = {"state": state, "per_page": bounded_limit, "sort": "updated", "direction": "desc"}
    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, headers=_headers(), params=params)
    if response.status_code >= 400:
        raise GitHubClientError(
            f"GitHub API error listing PRs: {response.status_code} {response.text[:500]}"
        )
    return [_normalize_pull_request(item) for item in response.json()[:bounded_limit]]


def get_pull_request(
    pr_number: str | int,
    *,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Resolve an existing open PR in the configured repository for GX10 handoff.

    Builder mutates the existing PR head branch, so fork-based PRs are rejected
    for now rather than accidentally pushing a similarly named branch into the
    configured origin repository.
    """
    owner, repo = _owner_repo(owner, repo)
    number = str(pr_number).strip()
    if not number.isdigit():
        raise GitHubClientError(f"Invalid pull request number: {pr_number!r}")
    url = f"{settings.github_api_base_url}/repos/{owner}/{repo}/pulls/{number}"
    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, headers=_headers())
    if response.status_code >= 400:
        raise GitHubClientError(
            f"GitHub API error loading PR {number}: {response.status_code} {response.text[:500]}"
        )
    data = response.json()
    head = data.get("head") or {}
    head_repo = head.get("repo") or {}
    expected_repo = f"{owner}/{repo}".lower()
    head_repo_name = str(head_repo.get("full_name") or "").lower()
    if head_repo_name and head_repo_name != expected_repo:
        raise GitHubClientError(
            f"PR #{number} comes from fork {head_repo.get('full_name')}; "
            "automatic mutation of fork PR branches is not enabled."
        )
    if data.get("state") != "open" or bool(data.get("merged")):
        raise GitHubClientError(f"PR #{number} is not open and cannot be actioned in place.")
    normalized = _normalize_pull_request(data)
    if not normalized["head_ref"]:
        raise GitHubClientError(f"PR #{number} has no usable head branch.")
    if not normalized["head_repo_full_name"]:
        normalized["head_repo_full_name"] = f"{owner}/{repo}"
    return normalized


def pull_request_is_open(
    pr_url: str,
    *,
    owner: str | None = None,
    repo: str | None = None,
) -> bool:
    """Return whether a Builder PR is still open and therefore safe to continue.

    A Slack Builder thread acts as one coding session only while the previously
    published PR remains open. Once that PR is merged/closed, the next turn
    starts a fresh branch from main instead of mutating historical work.
    """
    match = _PR_NUMBER_RE.search(pr_url or "")
    if not match:
        return False
    owner, repo = _owner_repo(owner, repo)
    url = f"{settings.github_api_base_url}/repos/{owner}/{repo}/pulls/{match.group(1)}"
    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, headers=_headers())
    if response.status_code >= 400:
        raise GitHubClientError(
            f"GitHub API error checking PR {match.group(1)}: "
            f"{response.status_code} {response.text[:300]}"
        )
    data = response.json()
    return data.get("state") == "open" and not bool(data.get("merged"))

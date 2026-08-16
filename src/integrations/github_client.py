"""Minimal GitHub REST client for Builder Agent pull-request operations."""

from __future__ import annotations

import logging
import re

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

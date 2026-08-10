"""Minimal GitHub REST client for Builder Agent pull-request creation."""

from __future__ import annotations

import logging

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)


class GitHubClientError(Exception):
    """Raised when the GitHub API returns an error creating a pull request."""


def create_pull_request(
    *,
    branch_name: str,
    title: str,
    body: str,
    base_branch: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, str]:
    """Open a PR from branch_name into base_branch via the GitHub REST API.

    Only PR creation goes through the API; pushing the branch is done via
    `git push` subprocess in the worker before this is called.
    """
    owner = owner or settings.github_repo_owner
    repo = repo or settings.github_repo_name
    base = base_branch or settings.builder_base_branch
    if not settings.github_token:
        raise GitHubClientError("GITHUB_TOKEN is not configured.")
    if not owner or not repo:
        raise GitHubClientError("GITHUB_REPO_OWNER/GITHUB_REPO_NAME are not configured.")

    url = f"{settings.github_api_base_url}/repos/{owner}/{repo}/pulls"
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"title": title, "head": branch_name, "base": base, "body": body}

    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, headers=headers, json=payload)

    if response.status_code >= 400:
        logger.error(
            "GitHub PR creation failed status=%s body=%s", response.status_code, response.text
        )
        raise GitHubClientError(f"GitHub API error {response.status_code}: {response.text[:500]}")

    data = response.json()
    return {"html_url": data["html_url"], "number": str(data["number"])}

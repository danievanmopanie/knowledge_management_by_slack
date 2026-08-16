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


def get_pull_request(
    pr_number: str | int,
    *,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Resolve an existing PR in the configured repository for GX10 handoff.

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
    head_ref = str(head.get("ref") or "").strip()
    if not head_ref:
        raise GitHubClientError(f"PR #{number} has no usable head branch.")
    return {
        "number": number,
        "html_url": data.get("html_url") or "",
        "title": data.get("title") or "",
        "body": data.get("body") or "",
        "state": data.get("state") or "",
        "head_ref": head_ref,
        "head_sha": head.get("sha") or "",
        "base_ref": (data.get("base") or {}).get("ref") or "",
        "head_repo_full_name": head_repo.get("full_name") or f"{owner}/{repo}",
    }


def pr_number_from_url(pr_url: str) -> str | None:
    """Extract the numeric PR id from a GitHub pull request URL, if present."""
    match = _PR_NUMBER_RE.search(pr_url or "")
    return match.group(1) if match else None


def get_pull_request_state(
    pr_number: str | int,
    *,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Lightweight PR state probe used by Merge & Deploy.

    Unlike `get_pull_request`, this does NOT raise on a closed/merged PR — the
    caller (deploy.py) needs to tell "already merged, safe to proceed to
    restart" apart from "closed without merging, nothing to deploy" apart from
    "genuinely still open," and `get_pull_request`'s open-only contract can't
    express that.
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
    return {
        "number": number,
        "state": data.get("state") or "",
        "merged": bool(data.get("merged")),
        "head_sha": head.get("sha") or "",
    }


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
    number = pr_number_from_url(pr_url)
    if not number:
        return False
    owner, repo = _owner_repo(owner, repo)
    url = f"{settings.github_api_base_url}/repos/{owner}/{repo}/pulls/{number}"
    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, headers=_headers())
    if response.status_code >= 400:
        raise GitHubClientError(
            f"GitHub API error checking PR {number}: {response.status_code} {response.text[:300]}"
        )
    data = response.json()
    return data.get("state") == "open" and not bool(data.get("merged"))


def merge_pull_request(
    pr_number: str | int,
    *,
    sha: str | None = None,
    merge_method: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Merge a pull request via the GitHub REST API.

    This is the deterministic Merge & Deploy path — never called from the AEON
    tool loop, only from an explicit, allowlist-checked Slack button click.
    Pinning `sha` (the PR's head commit at the time the card was last rendered)
    makes a stale merge fail safely with a 409 instead of silently merging
    commits the user never saw validated.
    """
    owner, repo = _owner_repo(owner, repo)
    number = str(pr_number).strip()
    if not number.isdigit():
        raise GitHubClientError(f"Invalid pull request number: {pr_number!r}")
    url = f"{settings.github_api_base_url}/repos/{owner}/{repo}/pulls/{number}/merge"
    payload: dict[str, Any] = {"merge_method": merge_method or settings.builder_merge_method}
    if sha:
        payload["sha"] = sha

    with httpx.Client(timeout=30.0) as client:
        response = client.put(url, headers=_headers(), json=payload)

    if response.status_code == 405:
        raise GitHubClientError(
            f"PR #{number} is not mergeable right now (branch protection, required checks, "
            f"or conflicts): {response.text[:500]}"
        )
    if response.status_code == 409:
        raise GitHubClientError(
            f"PR #{number}'s head branch moved since it was last checked; refusing to merge "
            f"commits that were never validated: {response.text[:500]}"
        )
    if response.status_code >= 400:
        raise GitHubClientError(
            f"GitHub API error merging PR {number}: {response.status_code} {response.text[:500]}"
        )

    data = response.json()
    return {
        "merged": bool(data.get("merged")),
        "sha": data.get("sha") or "",
        "message": data.get("message") or "",
    }

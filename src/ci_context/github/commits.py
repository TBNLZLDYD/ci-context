"""Commit/Diff data fetching — provide the triggering commit's diff summary.

For CI failure diagnosis the most valuable context after the error itself is
*what changed* right before the failure: the commit message tells intent, the
author and changed-file list tell which part of the codebase to suspect. This
module keeps the data layer lossless (full message + full SHA; first-line and
short-SHA truncation are rendering concerns), so downstream matchers and
renderers share one source of truth.
"""

from __future__ import annotations

import logging

from github.Commit import Commit as PyGithubCommit

from ci_context.github.client import GitHubClient
from ci_context.models.commit import ChangedFile, CommitInfo

logger = logging.getLogger(__name__)

# Commits in large repos can touch thousands of files (vendored code, merge
# commits, generated artifacts). A handful is usually enough to spot what broke
# the build, so cap the list to keep the report readable and avoid pulling
# pages of file data we would never display.
MAX_CHANGED_FILES = 50


def get_commit_context(client: GitHubClient, owner_repo: str, sha: str) -> CommitInfo:
    """
    Get a commit's context (message, author, changed-file diff summary).

    Args:
        client: GitHubClient instance
        owner_repo: Repository in "owner/repo" format
        sha: Full commit SHA

    Returns:
        CommitInfo dataclass

    A per-resource fetch failure (the requested commit is unknown, rate-limited,
    or the network fails) is caught and degrades to a stub CommitInfo that keeps
    the requested sha so the report can still reference the commit. A
    repository-level failure (client.get_repo: bad auth, repo not found) is NOT
    caught here and propagates to the caller — by design, so auth/not-found
    errors surface clearly instead of being silently stubbed.
    """
    repo = client.get_repo(owner_repo)
    try:
        commit: PyGithubCommit = repo.get_commit(sha)
    except Exception as e:
        # A failed fetch (404 unknown sha, rate limit, network) must not abort
        # the whole diagnosis — return a stub keeping the sha so the report can
        # still reference which commit we tried to inspect.
        logger.warning("Could not fetch commit %s in %s: %s", sha, owner_repo, e)
        return CommitInfo(sha=sha, message="", author="")

    # commit.files is a lazy PaginatedList whose first page (up to 300) is
    # already in memory from the get_commit response, so slicing to the cap
    # costs no additional network requests.
    changed_files = [
        ChangedFile(path=f.filename, additions=f.additions, deletions=f.deletions)
        for f in commit.files
    ][:MAX_CHANGED_FILES]

    return CommitInfo(
        sha=commit.sha or sha,
        message=commit.commit.message or "",
        author=commit.commit.author.name or "",
        changed_files=changed_files,
    )

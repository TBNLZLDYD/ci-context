"""GitHub API client — thin wrapper around PyGithub with auth and rate-limit awareness."""

from __future__ import annotations

from typing import Optional

from github import Github, Auth


class GitHubClient:
    """
    Centralized GitHub API client.

    Handles authentication, rate-limit tracking, and provides typed access
    to the GitHub REST API via PyGithub. All GitHub API interactions should
    go through this class so rate-limit state and auth are managed in one place.
    """

    def __init__(self, token: Optional[str] = None) -> None:
        # TODO: implement full auth flow (gh auth → GITHUB_TOKEN → GH_TOKEN → error)
        if token:
            self._client = Github(auth=Auth.Token(token))
        else:
            # Unauthenticated — will fail on most endpoints due to low rate limit
            self._client = Github()

    @property
    def rate_limit_remaining(self) -> int:
        """Remaining API calls in the current rate-limit window."""
        core = self._client.get_rate_limit().core
        return core.remaining

    def get_repo(self, owner_repo: str):
        """Fetch a repository by 'owner/repo' string."""
        return self._client.get_repo(owner_repo)

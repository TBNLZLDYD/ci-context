"""GitHub API client — thin wrapper around PyGithub with auth and rate-limit awareness."""

from __future__ import annotations

import logging

import httpx
from github import Auth, Github
from github.Repository import Repository

from ci_context.github.exceptions import RateLimitError

logger = logging.getLogger(__name__)


class GitHubClient:
    """
    Centralized GitHub API client.

    Handles authentication, rate-limit tracking, and provides typed access
    to the GitHub REST API via PyGithub. All GitHub API interactions should
    go through this class so rate-limit state and auth are managed in one place.

    Also provides httpx client for raw REST calls (e.g., job log downloads
    that PyGithub doesn't support).
    """

    def __init__(self, token: str, owner_repo: str | None = None) -> None:
        """
        Initialize GitHub client with authentication.

        Args:
            token: GitHub personal access token
            owner_repo: Optional repository in "owner/repo" format
        """
        self._token = token
        self._owner_repo = owner_repo
        self._pygithub = Github(auth=Auth.Token(token))
        self._httpx_client = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10.0,
            follow_redirects=True,
        )

    @property
    def pygithub(self) -> Github:
        """PyGithub instance for typed API access."""
        return self._pygithub

    @property
    def httpx_client(self) -> httpx.Client:
        """httpx client for raw REST calls (e.g., job logs)."""
        return self._httpx_client

    @property
    def token(self) -> str:
        """The token used for authentication."""
        return self._token

    @property
    def owner_repo(self) -> str | None:
        """Current repository in owner/repo format."""
        return self._owner_repo

    def set_owner_repo(self, owner_repo: str) -> None:
        """Set the current repository."""
        self._owner_repo = owner_repo

    def check_rate_limit(self, min_remaining: int = 10) -> None:
        """
        Check if we have sufficient rate limit remaining.

        Args:
            min_remaining: Minimum required remaining calls

        Raises:
            RateLimitError: If remaining calls is below threshold
        """
        rate = self._pygithub.get_rate_limit().rate
        if rate.remaining < min_remaining:
            # rate.reset is already a datetime; no round-trip through timestamp needed
            raise RateLimitError(rate.remaining, rate.reset)

    def get_repo(self, owner_repo: str) -> Repository:
        """Fetch a repository by 'owner/repo' string."""
        return self._pygithub.get_repo(owner_repo)

    def close(self) -> None:
        """Close httpx client connection."""
        self._httpx_client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        self.close()
        return None

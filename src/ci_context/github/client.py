"""GitHub API client — thin wrapper around PyGithub with auth and rate-limit awareness."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

import httpx
import requests.sessions
from github import Auth, Github
from github.Repository import Repository

from ci_context.github.exceptions import RateLimitError

logger = logging.getLogger(__name__)

# Proxy env vars that requests/httpx auto-detect; stripping them prevents
# system proxies (e.g., Clash on Windows) from causing SSL CERTIFICATE_VERIFY_FAILED.
# This alone is insufficient on dev-sidecar machines (see _ORIGINAL... below),
# because the *registry* system proxy is read by requests' get_environ_proxies().
_PROXY_ENV_KEYS = (
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "no_proxy", "NO_PROXY", "all_proxy", "ALL_PROXY",
)

# requests resolves ambient proxies via Session.merge_environment_settings() ->
# get_environ_proxies(), which on Windows falls back to
# urllib.request.getproxies() reading the *registry* system proxy
# (HKCU\...\Internet Settings). dev-sidecar/Clash set that registry key while
# leaving HTTP_PROXY env vars empty, so env-var stripping above cannot stop
# them from MITM-ing api.github.com (SSLCertVerificationError). The textbook
# fix would be Session.trust_env = False, but Session.__init__ hardcodes it
# True on each instance and PyGithub builds its Session lazily inside its
# connection, so the instance is unreachable before the first request. The only
# reliable lever is replacing get_environ_proxies itself — exactly the proxy
# half of trust_env=False, while REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE reading
# stays intact.
_requests_sessions: Any = requests.sessions
_ORIGINAL_ENVIRONMENT_PROXIES: Callable[[str, str | None], dict[str, str]] = (
    _requests_sessions.get_environ_proxies
)


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
        # PyGithub lazily creates its requests.Session on first API call, so
        # stripping proxy env vars only during Github() construction is insufficient.
        # We must keep them stripped for the client's entire lifetime; they are
        # restored in close() so the rest of the process is unaffected.
        self._saved_proxy_env = _strip_proxy_env()
        _suppress_registry_proxy()
        self._pygithub = Github(auth=Auth.Token(token))
        self._httpx_client = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10.0,
            follow_redirects=True,
            trust_env=False,
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
        """Close httpx client connection and restore proxy env vars."""
        self._httpx_client.close()
        # env vars first, then the lookup, so any session created after close()
        # sees a fully restored environment again.
        _restore_proxy_env(self._saved_proxy_env)
        _restore_registry_proxy()

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


def _strip_proxy_env() -> dict[str, str]:
    """Remove proxy env vars from os.environ and return the saved values.

    This is needed because PyGithub's underlying requests library auto-detects
    system proxy settings from env vars, and on Windows with tools like Clash
    this causes SSL CERTIFICATE_VERIFY_FAILED errors. We strip the vars only
    during Github() construction so the rest of the process is unaffected.
    """
    saved: dict[str, str] = {}
    for key in _PROXY_ENV_KEYS:
        val = os.environ.pop(key, None)
        if val is not None:
            saved[key] = val
    return saved


def _restore_proxy_env(saved: dict[str, str]) -> None:
    """Restore proxy env vars that were previously stripped."""
    os.environ.update(saved)


def _empty_environment_proxies(url: str, no_proxy: str | None = None) -> dict[str, str]:
    """Signature-compatible stand-in so requests sees a no-op proxy lookup.

    Named (instead of a lambda) so patching has a stable, mockable target and
    ruff/mypy can type-check the call signature without an ignore comment.
    """
    return {}


def _suppress_registry_proxy() -> None:
    """Hide the system proxy from every requests Session for the client's lifetime.

    PyGithub creates its requests.Session lazily on the first API call (inside
    HTTPSRequestsConnection), so a one-shot strip during Github() construction
    would miss it; the override must stay installed until close(). Patching the
    module-level lookup is process-wide, but that scope is acceptable here
    (same as the env-var strip above) and close() restores it for other apps.
    """
    _requests_sessions.get_environ_proxies = _empty_environment_proxies


def _restore_registry_proxy() -> None:
    """Restore the original proxy lookup after the client is done.

    Kept idempotent so a double close() (context-manager + explicit) cannot
    pin the no-op lookup into the process.
    """
    _requests_sessions.get_environ_proxies = _ORIGINAL_ENVIRONMENT_PROXIES

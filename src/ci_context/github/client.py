"""GitHub API client — thin wrapper around PyGithub with auth and rate-limit awareness."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any, TypeVar

import httpx
import requests.sessions
from github import Auth, Github
from github.GithubRetry import GithubRetry
from github.Repository import Repository

from ci_context.github.exceptions import RateLimitError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# "1 retry with 2s exponential backoff" for transient
# failures (connection error, timeout, 5xx). Centralized here so tests can
# patch ci_context.github.client.time.sleep (the real time.sleep is not safe
# in unit tests) and so the policy is visible from one place.
DEFAULT_RETRY_BACKOFF_SECONDS = 2.0
# 5xx is the universally-retried server-error class. 4xx is intentionally
# excluded — a 401/403/404 won't fix itself by retrying, and the GitHub
# rate-limit endpoint handles 429 separately in check_rate_limit().
RETRYABLE_HTTP_STATUSES = frozenset({500, 502, 503, 504})

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
        try:
            # timeout=10 enforces the 10s network budget per call.
            # retry=GithubRetry(total=1, backoff_factor=2.0) gives the exact
            # "1 retry after 2s" policy, overriding PyGithub's
            # default of 10 retries (which would multiply request latency by 10x
            # on transient failures and surprise the user).
            self._pygithub = Github(
                auth=Auth.Token(token),
                timeout=10,
                retry=GithubRetry(total=1, backoff_factor=DEFAULT_RETRY_BACKOFF_SECONDS),
            )
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
        except BaseException:
            # A raised __init__ means close() (and any `with` block) is never
            # reached, so the process-wide env strip + registry-proxy patch
            # above would leak into every later requests.Session of the host
            # process.  Restore it now; also close the httpx client if it was
            # already built, so its connection pool is not left open.
            _restore_proxy_state(self._saved_proxy_env)
            if hasattr(self, "_httpx_client"):
                self._httpx_client.close()
            raise

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

    def get(self, url: str, *, is_idempotent: bool = True) -> httpx.Response:
        """GET ``url`` with 1 retry on transient failures.

        Wraps ``httpx_client.get`` + ``raise_for_status`` in :func:`with_retry`
        so 5xx, timeouts, and connection errors get one automatic retry after
        :data:`DEFAULT_RETRY_BACKOFF_SECONDS` seconds. 4xx responses propagate
        without retrying — they signal caller error (bad URL, missing
        permission) that a second try cannot fix.

        Args:
            url: Path relative to the client's ``base_url``.
            is_idempotent: Set to False for write operations to skip retry
                entirely; auto-retrying a non-idempotent call risks duplicating
                its side effect.

        Returns:
            The :class:`httpx.Response` (status already checked).
        """
        def _do() -> httpx.Response:
            response = self._httpx_client.get(url)
            response.raise_for_status()
            return response

        return with_retry(_do, is_idempotent=is_idempotent)

    def close(self) -> None:
        """Close httpx client connection and restore proxy env vars."""
        self._httpx_client.close()
        # env vars first, then the lookup, so any session created after close()
        # sees a fully restored environment again.
        _restore_proxy_state(self._saved_proxy_env)

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


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


def with_retry(
    fn: Callable[[], T],
    *,
    is_idempotent: bool = True,
    backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> T:
    """Execute ``fn`` with one automatic retry on transient failure.

    Transient failures include connection errors, timeouts, and 5xx HTTP
    responses. 4xx responses are **never** retried — they signal caller
    error (bad URL / missing permission) that a second attempt cannot fix.
    Non-idempotent calls skip retry entirely to avoid duplicating side
    effects.

    The backoff sleep uses :data:`DEFAULT_RETRY_BACKOFF_SECONDS` by default
    so unit tests can patch it (or patch ``time.sleep`` itself) to avoid
    real wall-clock waiting.

    Args:
        fn: A zero-argument callable performing the request.
        is_idempotent: When False, skip retry unconditionally.
        backoff: Seconds to sleep before the retry.

    Returns:
        The return value of ``fn`` (type-preserved via ``T``).
    """
    try:
        return fn()
    except httpx.HTTPStatusError as exc:
        # 5xx is the only HTTP-level transient worth retrying. 4xx and
        # 1xx/2xx (which should not reach this path) propagate immediately.
        if not is_idempotent or exc.response.status_code not in RETRYABLE_HTTP_STATUSES:
            raise
        time.sleep(backoff)
        return fn()
    except (httpx.ConnectError, httpx.TimeoutException):
        # Network-level failures are always transient — retry once.
        if not is_idempotent:
            raise
        time.sleep(backoff)
        return fn()


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


def _restore_proxy_state(saved: dict[str, str]) -> None:
    """Undo both proxy patches: env vars first, then the lookup.

    Single restore point shared by :meth:`GitHubClient.close` and the
    ``__init__`` failure path so the ordering (env before lookup, so a Session
    created right after close sees a clean environment) cannot drift between
    the two cleanup routes.
    """
    _restore_proxy_env(saved)
    _restore_registry_proxy()

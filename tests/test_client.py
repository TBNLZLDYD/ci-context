"""Tests for GitHub API client."""

import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import httpx

from ci_context.github.client import GitHubClient
from ci_context.github.exceptions import RateLimitError


class TestGitHubClient(unittest.TestCase):
    """Test GitHubClient initialization and methods."""

    def test_initialization_with_token(self):
        """Should initialize with token."""
        with GitHubClient("test-token", "owner/repo") as client:
            self.assertEqual(client.token, "test-token")
            self.assertEqual(client.owner_repo, "owner/repo")

    def test_context_manager(self):
        """Should work as context manager."""
        with GitHubClient("test-token", "owner/repo") as client:
            self.assertIsNotNone(client.pygithub)
            self.assertIsNotNone(client.httpx_client)

    @patch("ci_context.github.client.Github")
    def test_check_rate_limit_sufficient(self, mock_github_cls):
        """Should not raise when rate limit is sufficient."""
        mock_rate = MagicMock()
        mock_rate.remaining = 100
        mock_rate.reset = datetime(2026, 7, 21, 10, 30, 0, tzinfo=UTC)
        mock_github = MagicMock()
        mock_github.get_rate_limit.return_value.rate = mock_rate
        mock_github_cls.return_value = mock_github

        client = GitHubClient("test-token")
        client.check_rate_limit(min_remaining=10)  # Should not raise

    @patch("ci_context.github.client.Github")
    def test_check_rate_limit_exceeded(self, mock_github_cls):
        """Should raise RateLimitError when rate limit is exceeded."""
        mock_rate = MagicMock()
        mock_rate.remaining = 5
        mock_rate.reset = datetime(2026, 7, 21, 10, 30, 0, tzinfo=UTC)
        mock_github = MagicMock()
        mock_github.get_rate_limit.return_value.rate = mock_rate
        mock_github_cls.return_value = mock_github

        client = GitHubClient("test-token")
        with self.assertRaises(RateLimitError) as ctx:
            client.check_rate_limit(min_remaining=10)
        # Verify reset_time uses rate.reset directly, no timestamp round-trip
        self.assertEqual(ctx.exception.reset_time, mock_rate.reset)

    @patch("ci_context.github.client.Github")
    def test_get_repo(self, mock_github_cls):
        """Should call pygithub get_repo."""
        mock_repo = MagicMock()
        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_repo
        mock_github_cls.return_value = mock_github

        client = GitHubClient("test-token")
        result = client.get_repo("owner/repo")
        mock_github.get_repo.assert_called_once_with("owner/repo")
        self.assertEqual(result, mock_repo)


class TestWithRetry(unittest.TestCase):
    """Tests for the with_retry module-level helper (PRD 13.2)."""

    def setUp(self):
        self.patcher = patch("ci_context.github.client.time.sleep")
        self.mock_sleep = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_success_on_first_try(self):
        """Should return fn result directly when no error occurs."""
        from ci_context.github.client import with_retry

        result = with_retry(lambda: "ok")
        self.assertEqual(result, "ok")
        self.mock_sleep.assert_not_called()

    def test_retries_once_on_5xx_then_succeeds(self):
        """5xx triggers one retry; if the second attempt succeeds, return it."""
        from ci_context.github.client import with_retry

        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.HTTPStatusError(
                    "Server Error",
                    request=MagicMock(),
                    response=MagicMock(status_code=502),
                )
            return "recovered"

        result = with_retry(fn)
        self.assertEqual(result, "recovered")
        self.assertEqual(calls, 2)
        self.mock_sleep.assert_called_once_with(2.0)

    def test_retry_exhausted_on_5xx_raises(self):
        """When both attempts on 5xx fail, the exception propagates."""
        from ci_context.github.client import with_retry

        exc = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=503),
        )

        with self.assertRaises(httpx.HTTPStatusError):
            with_retry(lambda: (_ for _ in ()).throw(exc))

        self.mock_sleep.assert_called_once_with(2.0)

    def test_4xx_does_not_retry(self):
        """4xx responses are never retried — they imply caller error."""
        from ci_context.github.client import with_retry

        exc = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        )

        with self.assertRaises(httpx.HTTPStatusError):
            with_retry(lambda: (_ for _ in ()).throw(exc))

        self.mock_sleep.assert_not_called()

    def test_connect_error_retries_once(self):
        """A connection error triggers one retry."""
        from ci_context.github.client import with_retry

        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("Connection refused")
            return "recovered"

        result = with_retry(fn)
        self.assertEqual(result, "recovered")
        self.assertEqual(calls, 2)
        self.mock_sleep.assert_called_once_with(2.0)

    def test_timeout_retries_once(self):
        """A timeout triggers one retry."""
        from ci_context.github.client import with_retry

        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.TimeoutException("Timed out")
            return "recovered"

        result = with_retry(fn)
        self.assertEqual(result, "recovered")
        self.assertEqual(calls, 2)
        self.mock_sleep.assert_called_once_with(2.0)

    def test_non_idempotent_skips_retry_on_5xx(self):
        """When is_idempotent=False, a 5xx is not retried."""
        from ci_context.github.client import with_retry

        exc = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=502),
        )

        with self.assertRaises(httpx.HTTPStatusError):
            with_retry(
                lambda: (_ for _ in ()).throw(exc),
                is_idempotent=False,
            )

        self.mock_sleep.assert_not_called()

    def test_non_idempotent_skips_retry_on_connect_error(self):
        """When is_idempotent=False, a ConnectError is not retried."""
        from ci_context.github.client import with_retry

        with self.assertRaises(httpx.ConnectError):
            with_retry(
                lambda: (_ for _ in ()).throw(httpx.ConnectError("Boom")),
                is_idempotent=False,
            )

        self.mock_sleep.assert_not_called()

    def test_non_idempotent_skips_retry_on_timeout(self):
        """When is_idempotent=False, a TimeoutException is not retried."""
        from ci_context.github.client import with_retry

        with self.assertRaises(httpx.TimeoutException):
            with_retry(
                lambda: (_ for _ in ()).throw(httpx.TimeoutException("Timed out")),
                is_idempotent=False,
            )

        self.mock_sleep.assert_not_called()

    def test_custom_backoff_is_passed_to_sleep(self):
        """The backoff parameter controls the sleep duration."""
        from ci_context.github.client import with_retry

        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("Boom")
            return "ok"

        result = with_retry(fn, backoff=1.5)
        self.assertEqual(result, "ok")
        self.mock_sleep.assert_called_once_with(1.5)


class TestGitHubClientGet(unittest.TestCase):
    """Tests for GitHubClient.get() — the httpx GET wrapper with retry."""

    @patch("ci_context.github.client.Github")
    def test_get_success(self, mock_github_cls):
        """get() should return the httpx response on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None

        client = GitHubClient("tok")
        client._httpx_client = MagicMock()
        client._httpx_client.get.return_value = mock_response

        result = client.get("/some/url")
        self.assertEqual(result, mock_response)
        client._httpx_client.get.assert_called_once_with("/some/url")

    @patch("ci_context.github.client.Github")
    def test_get_idempotent_true_by_default(self, mock_github_cls):
        """get() should default to is_idempotent=True so retry is active."""
        client = GitHubClient("tok")
        client._httpx_client = MagicMock()
        # First call fails with 502, second succeeds
        mock_response_ok = MagicMock()
        mock_response_ok.raise_for_status.return_value = None

        err_resp = MagicMock(status_code=502)

        client._httpx_client.get.side_effect = [
            httpx.HTTPStatusError("Err", request=MagicMock(), response=err_resp),
            mock_response_ok,
        ]

        with patch("ci_context.github.client.time.sleep") as mock_sleep:
            result = client.get("/path")
        self.assertEqual(result, mock_response_ok)
        mock_sleep.assert_called_once_with(2.0)

    @patch("ci_context.github.client.Github")
    def test_get_retries_on_connect_error(self, mock_github_cls):
        """get() retries once on a connection error, then returns."""
        client = GitHubClient("tok")
        client._httpx_client = MagicMock()
        mock_response_ok = MagicMock()
        mock_response_ok.raise_for_status.return_value = None

        client._httpx_client.get.side_effect = [
            httpx.ConnectError("Connection refused"),
            mock_response_ok,
        ]

        with patch("ci_context.github.client.time.sleep") as mock_sleep:
            result = client.get("/path")
        self.assertEqual(result, mock_response_ok)
        mock_sleep.assert_called_once_with(2.0)

    @patch("ci_context.github.client.Github")
    def test_get_404_no_retry(self, mock_github_cls):
        """4xx responses from get() propagate without retry."""
        client = GitHubClient("tok")
        client._httpx_client = MagicMock()
        err_resp = MagicMock(status_code=404)

        client._httpx_client.get.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=err_resp,
        )

        with self.assertRaises(httpx.HTTPStatusError):
            client.get("/missing")


class TestRateLimitError(unittest.TestCase):
    """Test RateLimitError exception."""

    def test_error_message_includes_reset_time(self):
        """Error message should include reset time."""
        error = RateLimitError(5, datetime(2026, 7, 21, 10, 30, 0))
        self.assertIn("5", error.message)
        self.assertIn("10:30", error.message)

    def test_error_message_utc_label_only_with_tzinfo(self):
        """UTC label should only appear when reset_time has tzinfo."""
        from datetime import UTC

        # Naive datetime: no UTC label to avoid misleading display
        naive = RateLimitError(5, datetime(2026, 7, 21, 10, 30, 0))
        self.assertIn("10:30", naive.message)
        self.assertNotIn("UTC", naive.message)

        # Timezone-aware datetime: UTC label is appropriate
        aware = RateLimitError(5, datetime(2026, 7, 21, 10, 30, 0, tzinfo=UTC))
        self.assertIn("10:30 UTC", aware.message)


if __name__ == "__main__":
    unittest.main()

"""Tests for GitHub API client."""

import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

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

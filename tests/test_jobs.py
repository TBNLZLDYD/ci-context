"""Tests for job data fetching and log retrieval."""

import unittest
from unittest.mock import MagicMock

import httpx

from ci_context.github.jobs import JobInfo, StepInfo, fetch_job_log, get_failed_jobs


class TestFetchJobLog(unittest.TestCase):
    """Test fetch_job_log error handling and log retrieval."""

    def _make_client(self) -> MagicMock:
        """Create a mock GitHubClient with a mock httpx_client."""
        client = MagicMock()
        return client

    def _make_httpx_error(
        self,
        status_code: int,
        message: str = "Error",
    ) -> httpx.HTTPStatusError:
        """Create an httpx.HTTPStatusError with a mock request/response."""
        mock_request = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = status_code
        return httpx.HTTPStatusError(message, request=mock_request, response=mock_response)

    def test_successful_log_fetch(self):
        """Should return raw log text on 200 response."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "line1\nline2\nline3"
        mock_response.raise_for_status.return_value = None
        client.get.return_value = mock_response

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertEqual(result, "line1\nline2\nline3")
        client.get.assert_called_once_with("/repos/owner/repo/actions/jobs/42/logs")

    def test_http_404_returns_none(self):
        """Should return None and log warning when logs are expired (404)."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = self._make_httpx_error(404, "Not Found")
        client.get.return_value = mock_response

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertIsNone(result)

    def test_http_429_returns_none(self):
        """Should return None and log warning when rate limited (429)."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = self._make_httpx_error(429, "Rate Limited")
        client.get.return_value = mock_response

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertIsNone(result)

    def test_http_500_returns_none(self):
        """Should return None and log warning on server error (5xx)."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = self._make_httpx_error(
            500, "Internal Server Error"
        )
        client.get.return_value = mock_response

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertIsNone(result)

    def test_timeout_returns_none(self):
        """Should return None and log warning on timeout."""
        client = self._make_client()
        client.get.side_effect = httpx.ReadTimeout("Timed out")

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertIsNone(result)

    def test_network_error_returns_none(self):
        """Should return None and log warning on network error."""
        client = self._make_client()
        client.get.side_effect = httpx.ConnectError("Connection refused")

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertIsNone(result)

    def test_empty_log_returns_marker(self):
        """Should return marker string for empty log content (PRD 13.2)."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = ""
        mock_response.raise_for_status.return_value = None
        client.get.return_value = mock_response

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertIn("No log output available", result)

    def test_whitespace_only_log_returns_marker(self):
        """Whitespace-only logs should also be treated as empty."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "   \n\n  \t  "
        mock_response.raise_for_status.return_value = None
        client.get.return_value = mock_response

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertIn("No log output available", result)

    def test_large_log_truncation(self):
        """Should truncate logs larger than 10MB, keeping first+last 1000 lines."""
        client = self._make_client()
        # 3000 lines x 4000 chars ~ 12MB, exceeds the 10MB threshold
        line = "x" * 4000
        large_log = "\n".join([line] * 3000)
        mock_response = MagicMock()
        mock_response.text = large_log
        mock_response.raise_for_status.return_value = None
        client.get.return_value = mock_response

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertIsNotNone(result)
        # Should contain the truncation marker with line count
        self.assertIn("skipped 1000 lines", result)
        # Result should be significantly smaller than input
        self.assertLess(len(result), len(large_log))

    def test_large_log_few_long_lines_truncation(self):
        """Should truncate logs > 10MB even when line count <= 2000."""
        client = self._make_client()
        # 100 lines x 150KB each = 15MB, but only 100 lines (< 2000)
        line = "x" * (150 * 1024)
        large_log = "\n".join([line] * 100)
        mock_response = MagicMock()
        mock_response.text = large_log
        mock_response.raise_for_status.return_value = None
        client.get.return_value = mock_response

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertIsNotNone(result)
        # Should contain the character-level truncation marker
        self.assertIn("truncated, log > 10 MB", result)
        # Result should be significantly smaller than input
        self.assertLess(len(result), len(large_log))

    def test_small_log_not_truncated(self):
        """Should not truncate logs under 10MB."""
        client = self._make_client()
        small_log = "\n".join(["normal line"] * 50)
        mock_response = MagicMock()
        mock_response.text = small_log
        mock_response.raise_for_status.return_value = None
        client.get.return_value = mock_response

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertEqual(result, small_log)

    def test_302_redirect_followed_automatically(self):
        """httpx with follow_redirects=True follows 302 transparently."""
        # When follow_redirects=True, httpx returns the final response,
        # so the caller never sees the 302. This test verifies that contract.
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "redirected log content"
        mock_response.raise_for_status.return_value = None
        client.get.return_value = mock_response

        result = fetch_job_log(client, "owner/repo", 42)
        self.assertEqual(result, "redirected log content")


class TestGetFailedJobs(unittest.TestCase):
    """Test get_failed_jobs filtering."""

    def test_filters_only_failed_jobs(self):
        """Should return only jobs with conclusion='failure'."""
        client = MagicMock()
        mock_run = MagicMock()

        failed_job = MagicMock()
        failed_job.conclusion = "failure"
        failed_job.name = "build"
        failed_job.id = 1
        failed_job.steps = []
        failed_job.started_at = None
        failed_job.completed_at = None

        success_job = MagicMock()
        success_job.conclusion = "success"
        success_job.name = "test"
        success_job.id = 2

        mock_run.jobs.return_value = [failed_job, success_job]
        client.get_repo.return_value.get_workflow_run.return_value = mock_run

        result = get_failed_jobs(client, "owner/repo", 123)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "build")

    def test_no_failed_jobs_returns_empty(self):
        """Should return empty list when all jobs succeeded."""
        client = MagicMock()
        mock_run = MagicMock()

        success_job = MagicMock()
        success_job.conclusion = "success"
        success_job.name = "test"
        success_job.id = 2

        mock_run.jobs.return_value = [success_job]
        client.get_repo.return_value.get_workflow_run.return_value = mock_run

        result = get_failed_jobs(client, "owner/repo", 123)
        self.assertEqual(result, [])

    def test_includes_timed_out_jobs(self):
        """Should include jobs with conclusion='timed_out' as failures."""
        client = MagicMock()
        mock_run = MagicMock()

        timed_out_job = MagicMock()
        timed_out_job.conclusion = "timed_out"
        timed_out_job.name = "build"
        timed_out_job.id = 3
        timed_out_job.steps = []
        timed_out_job.started_at = None
        timed_out_job.completed_at = None

        mock_run.jobs.return_value = [timed_out_job]
        client.get_repo.return_value.get_workflow_run.return_value = mock_run

        result = get_failed_jobs(client, "owner/repo", 123)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "build")


class TestJobInfo(unittest.TestCase):
    """Test JobInfo and StepInfo dataclasses."""

    def test_step_info_defaults(self):
        """StepInfo should store name, number, conclusion."""
        step = StepInfo(name="Build", number=1, conclusion="failure")
        self.assertEqual(step.name, "Build")
        self.assertEqual(step.number, 1)
        self.assertEqual(step.conclusion, "failure")

    def test_job_info_creation(self):
        """JobInfo should store id, name, conclusion, steps."""
        steps = [StepInfo(name="Build", number=1, conclusion="failure")]
        job = JobInfo(
            id=42,
            name="test-job",
            conclusion="failure",
            started_at=None,
            completed_at=None,
            steps=steps,
        )
        self.assertEqual(job.id, 42)
        self.assertEqual(len(job.steps), 1)


if __name__ == "__main__":
    unittest.main()

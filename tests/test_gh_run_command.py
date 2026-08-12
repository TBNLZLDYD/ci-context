"""Tests for `ci-context gh run` command — conclusion handling."""

from __future__ import annotations

import contextlib
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import typer

from ci_context.cli.gh import run_command
from ci_context.models.run import WorkflowRunInfo


def _make_run_info(conclusion: str | None, status: str = "completed") -> WorkflowRunInfo:
    """Create a WorkflowRunInfo with configurable conclusion."""
    return WorkflowRunInfo(
        id=12345,
        status=status,
        conclusion=conclusion,
        workflow_name="CI",
        head_sha="abc1234",
        event="push",
        created_at=datetime(2026, 1, 1),
        url="https://github.com/example/repo/actions/runs/12345",
        attempt=1,
        duration_seconds=60.0,
    )


def _make_mock_client() -> MagicMock:
    """Build a mock GitHubClient that works as a context manager."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.check_rate_limit = MagicMock()
    return mock_client


def _make_mock_ctx() -> MagicMock:
    """Build a mock Typer Context with obj={"verbose": False}."""
    mock_ctx = MagicMock()
    mock_ctx.obj = {"verbose": False}
    return mock_ctx


class TestRunCommandConclusionHandling(unittest.TestCase):
    """Verify that run_command handles all conclusion values correctly."""

    def _invoke_run_command(
        self, run_info: WorkflowRunInfo, force: bool = False
    ) -> MagicMock:
        """
        Call run_command with all GitHub dependencies mocked out.

        Returns the mock console so callers can assert on print calls.
        Sets self._exit_code to the typer.Exit code (or None if no exit).
        """
        mock_console = MagicMock()
        mock_client = _make_mock_client()

        with (
            patch("ci_context.cli.gh.resolve_token", return_value="fake-token"),
            patch("ci_context.cli.gh.resolve_repo", return_value="owner/repo"),
            patch("ci_context.cli.gh.GitHubClient", return_value=mock_client),
            patch("ci_context.cli.gh.get_run", return_value=run_info),
            patch("ci_context.cli.gh.get_failed_jobs", return_value=[]),
            patch("ci_context.cli.gh._print_poc_report"),
            patch("ci_context.cli.gh.console", mock_console),
        ):
            try:
                run_command(
                    ctx=_make_mock_ctx(),
                    run_id=12345,
                    repo=None,
                    attempt=None,
                    force=force,
                    no_history=False,
                    no_pr=False,
                    max_history=30,
                    error_lines=5,
                    json_output=False,
                    no_color=False,
                    token=None,
                )
            except typer.Exit as exc:
                self._exit_code = exc.exit_code
            else:
                self._exit_code = None

        return mock_console

    # ------------------------------------------------------------------
    # Test cases
    # ------------------------------------------------------------------

    def test_in_progress_prints_still_in_progress(self):
        """conclusion=None should print 'still in progress', not 'success'."""
        run_info = _make_run_info(conclusion=None, status="in_progress")
        console = self._invoke_run_command(run_info)

        printed = str(console.print.call_args_list)
        self.assertIn("still in progress", printed)
        self.assertNotIn("completed successfully", printed)
        self.assertNotIn("concluded with", printed)
        self.assertEqual(self._exit_code, 0)

    def test_success_prints_concluded_with_success(self):
        """conclusion='success' should print 'concluded with success'."""
        run_info = _make_run_info(conclusion="success")
        console = self._invoke_run_command(run_info)

        printed = str(console.print.call_args_list)
        self.assertIn("concluded with 'success'", printed)
        self.assertNotIn("still in progress", printed)
        self.assertEqual(self._exit_code, 0)

    def test_failure_proceeds_to_fetch_jobs(self):
        """conclusion='failure' should NOT exit early; should fetch failed jobs."""
        run_info = _make_run_info(conclusion="failure")

        mock_console = MagicMock()
        mock_client = _make_mock_client()

        with (
            patch("ci_context.cli.gh.resolve_token", return_value="fake-token"),
            patch("ci_context.cli.gh.resolve_repo", return_value="owner/repo"),
            patch("ci_context.cli.gh.GitHubClient", return_value=mock_client),
            patch("ci_context.cli.gh.get_run", return_value=run_info),
            patch("ci_context.cli.gh.get_failed_jobs", return_value=[]) as mock_jobs,
            patch("ci_context.cli.gh._print_poc_report") as mock_report,
            patch("ci_context.cli.gh.console", mock_console),
            contextlib.suppress(typer.Exit),
        ):
            run_command(
                ctx=_make_mock_ctx(),
                run_id=12345,
                repo=None,
                attempt=None,
                force=False,
                no_history=False,
                no_pr=False,
                max_history=30,
                error_lines=5,
                json_output=False,
                no_color=False,
                token=None,
            )

        mock_jobs.assert_called_once()
        mock_report.assert_called_once()

    def test_timed_out_proceeds_to_fetch_jobs(self):
        """conclusion='timed_out' should NOT exit early; should fetch failed jobs."""
        run_info = _make_run_info(conclusion="timed_out")

        mock_console = MagicMock()
        mock_client = _make_mock_client()

        with (
            patch("ci_context.cli.gh.resolve_token", return_value="fake-token"),
            patch("ci_context.cli.gh.resolve_repo", return_value="owner/repo"),
            patch("ci_context.cli.gh.GitHubClient", return_value=mock_client),
            patch("ci_context.cli.gh.get_run", return_value=run_info),
            patch("ci_context.cli.gh.get_failed_jobs", return_value=[]) as mock_jobs,
            patch("ci_context.cli.gh._print_poc_report") as mock_report,
            patch("ci_context.cli.gh.console", mock_console),
            contextlib.suppress(typer.Exit),
        ):
            run_command(
                ctx=_make_mock_ctx(),
                run_id=12345,
                repo=None,
                attempt=None,
                force=False,
                no_history=False,
                no_pr=False,
                max_history=30,
                error_lines=5,
                json_output=False,
                no_color=False,
                token=None,
            )

        mock_jobs.assert_called_once()
        mock_report.assert_called_once()

    def test_cancelled_prints_concluded_with_cancelled(self):
        """conclusion='cancelled' should print 'concluded with cancelled'."""
        run_info = _make_run_info(conclusion="cancelled")
        console = self._invoke_run_command(run_info)

        printed = str(console.print.call_args_list)
        self.assertIn("concluded with 'cancelled'", printed)
        self.assertNotIn("still in progress", printed)
        self.assertEqual(self._exit_code, 0)

    def test_in_progress_with_force_proceeds_to_fetch_jobs(self):
        """--force with conclusion=None should skip the in-progress guard."""
        run_info = _make_run_info(conclusion=None, status="in_progress")

        mock_console = MagicMock()
        mock_client = _make_mock_client()

        with (
            patch("ci_context.cli.gh.resolve_token", return_value="fake-token"),
            patch("ci_context.cli.gh.resolve_repo", return_value="owner/repo"),
            patch("ci_context.cli.gh.GitHubClient", return_value=mock_client),
            patch("ci_context.cli.gh.get_run", return_value=run_info),
            patch("ci_context.cli.gh.get_failed_jobs", return_value=[]) as mock_jobs,
            patch("ci_context.cli.gh._print_poc_report") as mock_report,
            patch("ci_context.cli.gh.console", mock_console),
            contextlib.suppress(typer.Exit),
        ):
            run_command(
                ctx=_make_mock_ctx(),
                run_id=12345,
                repo=None,
                attempt=None,
                force=True,
                no_history=False,
                no_pr=False,
                max_history=30,
                error_lines=5,
                json_output=False,
                no_color=False,
                token=None,
            )

        mock_jobs.assert_called_once()
        mock_report.assert_called_once()


if __name__ == "__main__":
    unittest.main()

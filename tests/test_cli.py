"""Tests for the CLI app structure, version flag, and recent/repo commands.

Deep coverage of `gh run` lives in test_gh_run_command.py; this module stays
focused on app wiring (sub-typers, --version, no-args help) plus end-to-end
invocation of `gh recent` / `gh repo` with the network layer mocked out.
"""

import json
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ci_context import __version__
from ci_context.cli.gh import _render_recent_failures
from ci_context.cli.main import app
from ci_context.github.exceptions import AuthError
from ci_context.models.run import WorkflowRunInfo


def _make_mock_client() -> MagicMock:
    """Build a mock GitHubClient that works as a context manager."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


def _make_run(id: int, conclusion: str, event: str, created_at: datetime) -> WorkflowRunInfo:
    """Build a real WorkflowRunInfo so the rendered JSON/table fields are assertable.

    A MagicMock run would satisfy isinstance checks but its attributes are
    MagicMocks, so asserting on the rendered output (ids, conclusions, URLs)
    would never actually compare against the values the test intends.
    """
    return WorkflowRunInfo(
        id=id,
        status="completed",
        conclusion=conclusion,
        workflow_name="CI",
        head_sha="abc",
        event=event,
        created_at=created_at,
        url=f"https://github.com/owner/repo/actions/runs/{id}",
    )


class TestCliAppStructure(unittest.TestCase):
    """Verify the root app wiring: sub-typers, version flag, and no-args help."""

    def _invoke(self, args: list[str]):
        """Invoke the app through CliRunner and return the result."""
        runner = CliRunner()
        return runner.invoke(app, args)

    def test_gh_subcommand_help_available(self):
        """`ci-context gh --help` must exit 0 and list the gh sub-commands."""
        result = self._invoke(["gh", "--help"])
        self.assertEqual(result.exit_code, 0)
        # Only the registered sub-command names are asserted — Typer's generated
        # help *descriptions* are formatting detail that can drift across versions.
        for expected in ("run", "recent", "repo"):
            self.assertIn(expected, result.stdout)

    def test_cache_subcommand_help_available(self):
        """`ci-context cache --help` must exit 0 and list the cache sub-commands."""
        result = self._invoke(["cache", "--help"])
        self.assertEqual(result.exit_code, 0)
        for expected in ("clear", "stats"):
            self.assertIn(expected, result.stdout)

    def test_version_flag_prints_version(self):
        """`ci-context --version` must exit 0 and print the exact version string."""
        result = self._invoke(["--version"])
        self.assertEqual(result.exit_code, 0)
        # Read the version from the package so a version bump can't silently
        # break this test (a hardcoded string would go stale).
        self.assertEqual(result.stdout.strip(), f"ci-context {__version__}")

    def test_no_args_shows_help(self):
        """Invoking with no command must print usage help (no_args_is_help=True)."""
        result = self._invoke([])
        # Typer's no_args_is_help exits with code 2; the help text must be present.
        self.assertEqual(result.exit_code, 2)
        self.assertIn("Usage", result.stdout)
        self.assertIn("COMMAND", result.stdout)


class TestRecentCommand(unittest.TestCase):
    """Test `gh recent` argument passing with the network layer mocked out."""

    def _invoke(self, args: list[str]):
        """Invoke `gh recent` and return the result, render mock, and client mock."""
        runner = CliRunner()
        mock_client = _make_mock_client()
        with (
            patch("ci_context.cli.gh.resolve_token", return_value="fake-token"),
            patch("ci_context.cli.gh.resolve_repo", return_value="owner/repo"),
            patch("ci_context.cli.gh.GitHubClient", return_value=mock_client),
            patch("ci_context.cli.gh._render_recent_failures") as mock_render,
            patch("ci_context.cli.gh.console"),
        ):
            result = runner.invoke(app, ["gh", "recent", *args])
        return result, mock_render, mock_client

    def test_recent_defaults(self):
        """Defaults must flow through: limit=10, no json, no color."""
        result, mock_render, mock_client = self._invoke([])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "")
        # Whole-signature assertion: positional indices would silently shift if
        # a parameter were inserted into _render_recent_failures.
        mock_render.assert_called_once_with(mock_client, "owner/repo", False, False, 10)

    def test_recent_json_and_limit_flags(self):
        """--json and --limit must be forwarded to the render function."""
        result, mock_render, mock_client = self._invoke(["--json", "--limit", "5"])
        self.assertEqual(result.exit_code, 0)
        mock_render.assert_called_once_with(mock_client, "owner/repo", True, False, 5)

    def test_recent_no_color_flag(self):
        """--no-color must be forwarded to the render function."""
        result, mock_render, mock_client = self._invoke(["--no-color"])
        self.assertEqual(result.exit_code, 0)
        mock_render.assert_called_once_with(mock_client, "owner/repo", False, True, 10)


class TestRepoCommand(unittest.TestCase):
    """Test `gh repo` positional-argument handling with the network layer mocked out."""

    def _invoke(self, args: list[str]):
        """Invoke `gh repo` and return the result, render/resolve mocks, and client mock."""
        runner = CliRunner()
        mock_client = _make_mock_client()
        with (
            patch("ci_context.cli.gh.resolve_token", return_value="fake-token"),
            patch("ci_context.cli.gh.resolve_repo", return_value="owner/repo") as mock_resolve,
            patch("ci_context.cli.gh.GitHubClient", return_value=mock_client),
            patch("ci_context.cli.gh._render_recent_failures") as mock_render,
            patch("ci_context.cli.gh.console"),
        ):
            result = runner.invoke(app, ["gh", "repo", *args])
        return result, mock_render, mock_resolve, mock_client

    def test_repo_positional_passed_to_resolve_repo(self):
        """The positional owner/repo argument must be handed to resolve_repo."""
        result, mock_render, mock_resolve, mock_client = self._invoke(["foo/bar"])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "")
        mock_resolve.assert_called_once_with("foo/bar")
        mock_render.assert_called_once_with(mock_client, "owner/repo", False, False, 10)

    def test_repo_json_and_limit_flags(self):
        """--json and --limit must be forwarded to the render function."""
        result, mock_render, mock_resolve, mock_client = self._invoke(
            ["foo/bar", "--json", "--limit", "7"]
        )
        self.assertEqual(result.exit_code, 0)
        mock_resolve.assert_called_once_with("foo/bar")
        mock_render.assert_called_once_with(mock_client, "owner/repo", True, False, 7)


class TestRenderRecentFailures(unittest.TestCase):
    """Direct unit tests for _render_recent_failures (shared by `gh recent`/`gh repo`).

    The CLI-level tests above mock this function out entirely; these tests call
    it directly with real WorkflowRunInfo instances so the rendered JSON/table
    fields and the count-clamping logic are actually exercised.
    """

    def _render(self, runs, *, json_output, no_color=False, limit=10):
        """Run _render_recent_failures with the network list mocked and echo captured."""
        client = MagicMock()
        with (
            patch("ci_context.cli.gh.list_workflow_runs", return_value=runs) as mock_list,
            patch("ci_context.cli.gh.typer.echo") as mock_echo,
        ):
            _render_recent_failures(client, "owner/repo", json_output, no_color, limit)
        return mock_list, mock_echo, client

    def test_json_branch_exposes_all_top_level_fields(self):
        """JSON output must carry repo/rates/trend plus the failed-run details."""
        runs = [
            _make_run(3, "failure", "push", datetime(2026, 1, 3, 10, 0, 0)),
            _make_run(2, "success", "push", datetime(2026, 1, 2, 10, 0, 0)),
            _make_run(1, "timed_out", "pull_request", datetime(2026, 1, 1, 10, 0, 0)),
        ]
        _, mock_echo, _ = self._render(runs, json_output=True)
        payload = json.loads(mock_echo.call_args.args[0])

        # 2 of the 3 runs fail (failure + timed_out): 2/3 rounds to 67%.
        self.assertEqual(payload["repo"], "owner/repo")
        self.assertEqual(payload["total_runs"], 3)
        self.assertEqual(payload["failed_runs"], 2)
        self.assertEqual(payload["failure_rate"], "67%")
        self.assertEqual(payload["recent_failure_rate"], "67%")
        # Trend is delegated to compute_trend; recent == overall here so it is "stable".
        self.assertEqual(payload["trend"], "stable")

        # Only the two failed runs belong in recent_failed_runs, in run order.
        self.assertEqual([r["id"] for r in payload["recent_failed_runs"]], [3, 1])
        for entry in payload["recent_failed_runs"]:
            self.assertEqual(
                set(entry.keys()),
                {"id", "workflow_name", "event", "conclusion", "created_at", "url"},
            )
        self.assertEqual(payload["recent_failed_runs"][0]["created_at"], "2026-01-03T10:00:00Z")
        self.assertEqual(payload["recent_failed_runs"][0]["conclusion"], "failure")
        self.assertEqual(payload["recent_failed_runs"][1]["conclusion"], "timed_out")

    def test_table_branch_renders_failed_run_rows(self):
        """Table output must show the title, failed run ids, and the failure-rate line."""
        runs = [
            _make_run(99, "failure", "push", datetime(2026, 1, 3, 10, 0, 0)),
            _make_run(98, "success", "push", datetime(2026, 1, 2, 10, 0, 0)),
        ]
        _, mock_echo, _ = self._render(runs, json_output=False)
        out = mock_echo.call_args.args[0]
        self.assertIn("Recent Failed Runs — owner/repo", out)
        self.assertIn("99", out)
        self.assertIn("Failure rate:", out)
        self.assertIn("50%", out)

    def test_table_branch_no_failed_runs_shows_placeholder(self):
        """With no failed runs the table must print the (no failed runs found) placeholder."""
        runs = [_make_run(5, "success", "push", datetime(2026, 1, 1, 10, 0, 0))]
        _, mock_echo, _ = self._render(runs, json_output=False)
        out = mock_echo.call_args.args[0]
        self.assertIn("(no failed runs found)", out)

    def test_negative_limit_is_clamped(self):
        """limit=-5 must clamp the fetch count to max(-25, 30) == 30 and not crash."""
        mock_list, _, client = self._render([], json_output=False, limit=-5)
        mock_list.assert_called_once_with(client, "owner/repo", workflow_id=None, count=30)


class TestRecentRepoErrorPaths(unittest.TestCase):
    """Verify `gh recent`/`gh repo` surface auth and repo-resolution failures as exit 1."""

    def _invoke(self, args: list[str]):
        """Invoke the app through CliRunner and return the result."""
        runner = CliRunner()
        return runner.invoke(app, args)

    def test_recent_auth_error_exits_1(self):
        """A resolve_token AuthError in `gh recent` must exit non-zero."""
        with (
            patch("ci_context.cli.gh.resolve_token", side_effect=AuthError("no token")),
            patch("ci_context.cli.gh.console"),
        ):
            result = self._invoke(["gh", "recent"])
        self.assertEqual(result.exit_code, 1)

    def test_recent_resolve_repo_error_exits_1(self):
        """A resolve_repo ValueError in `gh recent` must exit non-zero."""
        with (
            patch("ci_context.cli.gh.resolve_token", return_value="fake-token"),
            patch(
                "ci_context.cli.gh.resolve_repo",
                side_effect=ValueError("Invalid repository format: bad"),
            ),
            patch("ci_context.cli.gh.console"),
        ):
            result = self._invoke(["gh", "recent"])
        self.assertEqual(result.exit_code, 1)

    def test_repo_auth_error_exits_1(self):
        """A resolve_token AuthError in `gh repo` must exit non-zero."""
        with (
            patch("ci_context.cli.gh.resolve_token", side_effect=AuthError("no token")),
            patch("ci_context.cli.gh.console"),
        ):
            result = self._invoke(["gh", "repo", "owner/repo"])
        self.assertEqual(result.exit_code, 1)

    def test_repo_resolve_repo_error_exits_1(self):
        """A resolve_repo ValueError in `gh repo` must exit non-zero."""
        with (
            patch("ci_context.cli.gh.resolve_token", return_value="fake-token"),
            patch(
                "ci_context.cli.gh.resolve_repo",
                side_effect=ValueError("Invalid repository format: bad"),
            ),
            patch("ci_context.cli.gh.console"),
        ):
            result = self._invoke(["gh", "repo", "owner/repo"])
        self.assertEqual(result.exit_code, 1)


if __name__ == "__main__":
    unittest.main()

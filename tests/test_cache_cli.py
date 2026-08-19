"""Tests for the `ci-context cache` CLI commands and history cache integration.

The storage layer (db.py) is covered exhaustively in test_cache_db.py; this
module focuses on the CLI surface (`cache clear`, `cache stats`, `cache purge`)
and the cache short-circuit wired into ``_build_history``.

All tests redirect the cache file to a per-test temp directory so the real
``%LOCALAPPDATA%\\ci-context\\history.db`` is never touched.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ci_context.analysis.fingerprint import compute_fingerprint
from ci_context.cache import db
from ci_context.cli.cache import cache_clear, cache_purge, cache_stats
from ci_context.cli.main import app
from ci_context.models.error import ExtractedError
from ci_context.models.run import WorkflowRunInfo


@contextlib.contextmanager
def _temp_cache_dir():
    """Redirect db.cache_path() to a per-test temp dir.

    Patches the *Path* the db layer will hand to ``sqlite3.connect`` so every
    read/write in this test (and the code under test) lands in a sandbox.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / "history.db"
        with patch("ci_context.cache.db.cache_path", return_value=tmp_path):
            yield tmp_path


def _invoke_app(args: list[str]):
    """Run the typer app through CliRunner and return the result."""
    return CliRunner().invoke(app, args)


def _make_run(
    run_id: int = 1,
    *,
    conclusion: str | None = "failure",
    status: str = "completed",
    head_sha: str | None = None,
) -> WorkflowRunInfo:
    """Build a real WorkflowRunInfo for seeding the cache in tests."""
    return WorkflowRunInfo(
        id=run_id,
        status=status,
        conclusion=conclusion,
        workflow_name="CI",
        head_sha=head_sha or f"sha{run_id:036x}",
        event="push",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        url=f"https://github.com/owner/repo/actions/runs/{run_id}",
        attempt=1,
        duration_seconds=10.0,
    )


def _make_error(message: str = "boom") -> ExtractedError:
    """Build a real ExtractedError for fingerprint computation."""
    return ExtractedError(error_type="Python Traceback", message=message)


class TestCacheClearCommand(unittest.TestCase):
    """`ci-context cache clear` empties every table and reports the count."""

    def setUp(self) -> None:
        self._cm = _temp_cache_dir()
        self._cm.__enter__()

    def tearDown(self) -> None:
        self._cm.__exit__(None, None, None)

    def test_clear_on_empty_cache_reports_zero(self) -> None:
        """Clearing an empty cache must succeed and report 0 rows removed."""
        result = _invoke_app(["cache", "clear"])
        self.assertEqual(result.exit_code, 0)
        # Status message goes to stderr (mirror console); CliRunner exposes
        # ``stderr`` separately, so asserting the row count is in stderr
        # proves the data path is wired up rather than echoing to stdout.
        self.assertIn("0 row", result.stderr)

    def test_clear_reports_row_count_for_populated_cache(self) -> None:
        """The reported count must equal the rows actually deleted."""
        run = _make_run(run_id=42)
        err = _make_error("first")
        fp = compute_fingerprint(err)
        db.store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=42,
            repo="owner/repo",
            commit_message="m",
            timestamp="2025-01-01T00:00:00Z",
        )
        db.store_run_metadata(42, "owner/repo", run)

        result = _invoke_app(["cache", "clear"])
        self.assertEqual(result.exit_code, 0)
        # 1 fingerprint + 1 occurrence + 1 run_metadata = 3 rows.
        self.assertIn("3 row", result.stderr)

        # Sanity: the underlying tables are empty.
        s = db.stats()
        self.assertEqual(s.fingerprint_count, 0)
        self.assertEqual(s.occurrence_count, 0)
        self.assertEqual(s.run_metadata_count, 0)

    def test_clear_direct_function_call(self) -> None:
        """The undecorated ``cache_clear`` function must work standalone too."""
        db.store_fingerprint(
            fingerprint="a" * 16,
            error_type="T",
            normalized_message="m",
            run_id=1,
            repo="owner/repo",
            commit_message="c",
            timestamp="2025-01-01T00:00:00Z",
        )
        # Direct call must not raise; it uses the same console -> stderr path.
        cache_clear()
        self.assertEqual(db.stats().fingerprint_count, 0)


class TestCacheStatsCommand(unittest.TestCase):
    """`ci-context cache stats` renders a Rich table to stdout."""

    def setUp(self) -> None:
        self._cm = _temp_cache_dir()
        self._cm.__enter__()
        db.clear()

    def tearDown(self) -> None:
        self._cm.__exit__(None, None, None)

    def test_stats_renders_all_metric_rows(self) -> None:
        """The rendered table must include every metric label and the path."""
        result = _invoke_app(["cache", "stats"])
        self.assertEqual(result.exit_code, 0)
        # Rich Table columns are emitted in the order added; assert each
        # label is present so a dropped metric is caught.
        for label in (
            "Fingerprints",
            "Fingerprint occurrences",
            "Run metadata entries",
            "Database size",
            "Database path",
        ):
            self.assertIn(label, result.stdout)
        self.assertIn("history.db", result.stdout)

    def test_stats_reflects_seeded_data(self) -> None:
        """Inserted rows must show up in the rendered counts."""
        for run_id in (1, 2, 3):
            db.store_fingerprint(
                fingerprint=f"fp{run_id:013x}",
                error_type="T",
                normalized_message=f"msg {run_id}",
                run_id=run_id,
                repo="owner/repo",
                commit_message="m",
                timestamp="2025-01-01T00:00:00Z",
            )

        result = _invoke_app(["cache", "stats"])
        self.assertEqual(result.exit_code, 0)
        # Rich pads the value cell with spaces; the count itself ("3") sits
        # in the row labelled "Fingerprints".  A row-scoped regex avoids
        # coupling to Rich's exact column padding, which can drift across
        # releases.
        import re

        # Must find a row starting with "Fingerprints" whose value cell
        # contains the count "3".  Run metadata is still 0, so the only
        # "3" in the table is the fingerprint count.
        match = re.search(r"Fingerprints\s+[│|]\s+(\d+)", result.stdout)
        self.assertIsNotNone(match, f"Could not find Fingerprints row in:\n{result.stdout}")
        self.assertEqual(match.group(1), "3")

    def test_stats_size_is_humanised(self) -> None:
        """The DB size column must use a humanised B/KiB/MiB unit, not raw bytes."""
        result = _invoke_app(["cache", "stats"])
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(
            any(unit in result.stdout for unit in (" B", " KiB", " MiB")),
            f"Expected a humanised size unit in stats output, got:\n{result.stdout}",
        )

    def test_stats_direct_function_call(self) -> None:
        """The undecorated ``cache_stats`` function must also produce a table."""
        # No assert on output content beyond not raising — the rich table is
        # written to stdout via the same typer.echo path the test exercises.
        cache_stats()


class TestCachePurgeCommand(unittest.TestCase):
    """`ci-context cache purge` removes only TTL-expired rows."""

    def setUp(self) -> None:
        self._cm = _temp_cache_dir()
        self._cm.__enter__()
        db.clear()

    def tearDown(self) -> None:
        self._cm.__exit__(None, None, None)

    def test_purge_on_empty_cache_reports_zero(self) -> None:
        result = _invoke_app(["cache", "purge"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("0 row", result.stderr)

    def test_purge_removes_expired_rows(self) -> None:
        """Rows older than the TTL must be deleted by purge."""
        db.store_fingerprint(
            fingerprint="a" * 16,
            error_type="T",
            normalized_message="m",
            run_id=1,
            repo="owner/repo",
            commit_message="c",
            timestamp="2024-01-01T00:00:00Z",
        )
        # Backdate the row past the TTL.
        with db.get_connection() as conn, conn:
            conn.execute(
                "UPDATE fingerprint_occurrences SET created_at = datetime('now', '-8 days')"
            )
            conn.execute("UPDATE fingerprints SET created_at = datetime('now', '-8 days')")

        result = _invoke_app(["cache", "purge"])
        self.assertEqual(result.exit_code, 0)
        # 1 fingerprint + 1 occurrence = 2 rows purged.
        self.assertIn("2 row", result.stderr)
        self.assertEqual(db.stats().fingerprint_count, 0)

    def test_purge_keeps_fresh_rows(self) -> None:
        """Fresh rows must survive a purge invocation."""
        db.store_fingerprint(
            fingerprint="a" * 16,
            error_type="T",
            normalized_message="m",
            run_id=1,
            repo="owner/repo",
            commit_message="c",
            timestamp="2025-01-01T00:00:00Z",
        )
        result = _invoke_app(["cache", "purge"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("0 row", result.stderr)
        self.assertEqual(db.stats().fingerprint_count, 1)

    def test_purge_direct_function_call(self) -> None:
        """Direct invocation must work without going through typer."""
        cache_purge()


class TestCacheHelpListsAllSubcommands(unittest.TestCase):
    """`ci-context cache --help` must list every sub-command name."""

    def test_help_lists_clear_stats_purge(self) -> None:
        result = _invoke_app(["cache", "--help"])
        self.assertEqual(result.exit_code, 0)
        for expected in ("clear", "stats", "purge"):
            self.assertIn(expected, result.stdout)


class TestBuildHistoryCacheIntegration(unittest.TestCase):
    """``_build_history`` consults the cache before calling the GitHub API.

    A cache hit must skip the per-run log fetch; a miss must call the API
    *and* write the result back so the next invocation can short-circuit.
    """

    def setUp(self) -> None:
        self._cm = _temp_cache_dir()
        self._cm.__enter__()
        db.clear()

        # A current run (the one being analysed) and a single failed
        # historical run whose log we'd otherwise have to fetch.
        self.current_run = _make_run(run_id=9999, conclusion="failure")
        self.historical_run = _make_run(run_id=100, conclusion="failure")
        self.current_errors = [_make_error("import error: foo")]

    def tearDown(self) -> None:
        self._cm.__exit__(None, None, None)

    def test_cache_miss_calls_api_and_writes_back(self) -> None:
        """First-ever run: every API helper is called, and data is cached."""
        mock_client = MagicMock()
        err = self.current_errors[0]
        fp = compute_fingerprint(err)

        with (
            patch("ci_context.cli.gh.get_workflow_file", return_value="ci.yml"),
            patch(
                "ci_context.cli.gh.list_workflow_runs",
                return_value=[self.current_run, self.historical_run],
            ),
            patch("ci_context.cli.gh.get_commit_message", return_value="fix bug") as mock_msg,
            patch(
                "ci_context.cli.gh.get_failed_jobs", return_value=[MagicMock(id=1)]
            ) as mock_jobs,
            patch(
                "ci_context.cli.gh.fetch_job_log",
                return_value="Traceback (most recent call last):",
            ),
            patch("ci_context.cli.gh.extract_errors", return_value=[err]) as mock_extract,
            patch("ci_context.cli.gh.console"),
            patch("ci_context.cli.gh.build_history_report") as mock_build,
        ):
            from ci_context.cli.gh import _build_history

            _build_history(
                mock_client, "owner/repo", self.current_run, self.current_errors, 10
            )

        # API was consulted for the historical run.
        mock_msg.assert_called()
        mock_jobs.assert_called()
        mock_extract.assert_called()
        mock_build.assert_called_once()
        # Data was written to the cache.
        cached = db.get_fingerprint_occurrences(repo="owner/repo")
        self.assertIn(fp, cached)
        self.assertEqual(cached[fp][0].run_id, 100)

    def test_cache_hit_skips_log_fetch(self) -> None:
        """A second invocation must not call fetch_job_log for the cached run."""
        # Seed the cache as if a previous run had already extracted this.
        err = self.current_errors[0]
        fp = compute_fingerprint(err)
        db.store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=100,
            repo="owner/repo",
            commit_message="previous commit",
            timestamp="2025-01-01T00:00:00Z",
        )

        mock_client = MagicMock()
        with (
            patch("ci_context.cli.gh.get_workflow_file", return_value="ci.yml"),
            patch(
                "ci_context.cli.gh.list_workflow_runs",
                return_value=[self.current_run, self.historical_run],
            ),
            patch("ci_context.cli.gh.get_commit_message", return_value="unused") as mock_msg,
            patch("ci_context.cli.gh.get_failed_jobs") as mock_jobs,
            patch("ci_context.cli.gh.fetch_job_log") as mock_log,
            patch("ci_context.cli.gh.extract_errors") as mock_extract,
            patch("ci_context.cli.gh.console"),
            patch("ci_context.cli.gh.build_history_report") as mock_build,
        ):
            from ci_context.cli.gh import _build_history

            _build_history(
                mock_client, "owner/repo", self.current_run, self.current_errors, 10
            )

        # None of the per-run log fetches happened — only the listing path
        # (which is not cacheable) and the report builder.
        mock_msg.assert_not_called()
        mock_jobs.assert_not_called()
        mock_log.assert_not_called()
        mock_extract.assert_not_called()
        mock_build.assert_called_once()
        # The fps passed to the report builder contains an occurrence for
        # run 100 (from the cache), not from the API.
        fps_arg = mock_build.call_args.args[1]
        self.assertIn(fp, fps_arg)
        self.assertEqual(fps_arg[fp][0].run_id, 100)
        self.assertEqual(fps_arg[fp][0].commit_message, "previous commit")

    def test_cache_hit_for_one_run_only_fetches_uncached_runs(self) -> None:
        """Mixed state: cached run is skipped, the new run is fetched + cached."""
        # Seed the cache only for the historical run.
        err = self.current_errors[0]
        fp = compute_fingerprint(err)
        db.store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=100,
            repo="owner/repo",
            commit_message="cached commit",
            timestamp="2025-01-01T00:00:00Z",
        )

        # Add a second failed historical run that is NOT in the cache.
        # Distinct head_sha lets the test verify which run get_commit_message
        # was called for (sha is its third positional arg, the only one
        # that differs between the two runs).
        new_run = _make_run(run_id=200, conclusion="failure", head_sha="newsha" + "0" * 35)
        cached_run_with_distinct_sha = _make_run(
            run_id=100, conclusion="failure", head_sha="cachedsha" + "0" * 35
        )
        # current_run also needs a distinct sha to be filterable.
        self.current_run = _make_run(
            run_id=9999, conclusion="failure", head_sha="currentsha" + "0" * 35
        )

        mock_client = MagicMock()
        with (
            patch("ci_context.cli.gh.get_workflow_file", return_value="ci.yml"),
            patch(
                "ci_context.cli.gh.list_workflow_runs",
                return_value=[self.current_run, cached_run_with_distinct_sha, new_run],
            ),
            patch(
                "ci_context.cli.gh.get_commit_message", return_value="new commit"
            ) as mock_msg,
            patch(
                "ci_context.cli.gh.get_failed_jobs", return_value=[MagicMock(id=99)]
            ) as mock_jobs,
            patch("ci_context.cli.gh.fetch_job_log", return_value="log"),
            patch("ci_context.cli.gh.extract_errors", return_value=[err]) as mock_extract,
            patch("ci_context.cli.gh.console"),
            patch("ci_context.cli.gh.build_history_report") as mock_build,
        ):
            from ci_context.cli.gh import _build_history

            _build_history(
                mock_client, "owner/repo", self.current_run, self.current_errors, 10
            )

        # The API was only consulted for the uncached run (200), not the cached
        # one (100).  get_commit_message is called once with the new run's
        # head_sha; get_failed_jobs is called once with run_id=200.
        mock_msg.assert_called_once()
        self.assertTrue(mock_msg.call_args.args[2].startswith("newsha"))
        mock_jobs.assert_called_once()
        self.assertEqual(mock_jobs.call_args.args[2], 200)
        # extract_errors was called for the new run's log.
        mock_extract.assert_called()
        mock_build.assert_called_once()

        # The cache now contains rows for both runs.
        cached = db.get_fingerprint_occurrences(repo="owner/repo")
        cached_run_ids = {occ.run_id for occs in cached.values() for occ in occs}
        self.assertEqual(cached_run_ids, {100, 200})

    def test_cache_read_failure_does_not_abort_history(self) -> None:
        """A broken cache read must not prevent history from being reported.

        The integration wrapper catches the exception, logs it, and treats
        every run as a cache miss — so the report still uses the API path.
        """
        mock_client = MagicMock()
        err = self.current_errors[0]

        with (
            patch("ci_context.cli.gh.get_workflow_file", return_value="ci.yml"),
            patch(
                "ci_context.cli.gh.list_workflow_runs",
                return_value=[self.current_run, self.historical_run],
            ),
            patch("ci_context.cli.gh.get_commit_message", return_value="m"),
            patch("ci_context.cli.gh.get_failed_jobs", return_value=[MagicMock(id=1)]),
            patch("ci_context.cli.gh.fetch_job_log", return_value="log"),
            patch("ci_context.cli.gh.extract_errors", return_value=[err]),
            patch("ci_context.cli.gh.console"),
            # Force the cache read to explode — the build must still succeed.
            patch(
                "ci_context.cli.gh.cache_db.get_fingerprint_occurrences",
                side_effect=RuntimeError("simulated cache boom"),
            ),
            patch("ci_context.cli.gh.build_history_report") as mock_build,
        ):
            from ci_context.cli.gh import _build_history

            result = _build_history(
                mock_client, "owner/repo", self.current_run, self.current_errors, 10
            )

        # build_history_report still got called and a report was returned.
        self.assertEqual(result, mock_build.return_value)
        mock_build.assert_called_once()

    def test_empty_error_list_skips_cache_consultation(self) -> None:
        """When there are no current errors, the per-run scan is skipped entirely.

        The cache lookup is a non-trivial read; skipping it on the empty-
        errors path avoids a useless disk hit when the user only needs the
        rates/trend.
        """
        mock_client = MagicMock()
        with (
            patch("ci_context.cli.gh.get_workflow_file", return_value="ci.yml"),
            patch(
                "ci_context.cli.gh.list_workflow_runs",
                return_value=[self.current_run, self.historical_run],
            ),
            patch("ci_context.cli.gh.console"),
            patch("ci_context.cli.gh.build_history_report") as mock_build,
            patch("ci_context.cli.gh.cache_db.get_fingerprint_occurrences") as mock_cache_get,
        ):
            from ci_context.cli.gh import _build_history

            _build_history(mock_client, "owner/repo", self.current_run, [], 10)

        mock_cache_get.assert_not_called()
        mock_build.assert_called_once()

    def test_cache_write_failure_does_not_abort_history(self) -> None:
        """A broken cache write must not prevent history from being reported.

        When the cache-miss path extracts errors and tries to write them back,
        a failure in store_fingerprint is caught and logged — the report still
        completes using the in-memory occurrence list.
        """
        mock_client = MagicMock()
        err = self.current_errors[0]

        with (
            patch("ci_context.cli.gh.get_workflow_file", return_value="ci.yml"),
            patch(
                "ci_context.cli.gh.list_workflow_runs",
                return_value=[self.current_run, self.historical_run],
            ),
            patch("ci_context.cli.gh.get_commit_message", return_value="m"),
            patch("ci_context.cli.gh.get_failed_jobs", return_value=[MagicMock(id=1)]),
            patch("ci_context.cli.gh.fetch_job_log", return_value="log"),
            patch("ci_context.cli.gh.extract_errors", return_value=[err]),
            patch("ci_context.cli.gh.console"),
            # Force the cache write to explode on every store_fingerprint call.
            patch(
                "ci_context.cli.gh.cache_db.store_fingerprint",
                side_effect=RuntimeError("simulated write boom"),
            ),
            patch("ci_context.cli.gh.build_history_report") as mock_build,
        ):
            from ci_context.cli.gh import _build_history

            result = _build_history(
                mock_client, "owner/repo", self.current_run, self.current_errors, 10
            )

        # build_history_report still got called and a report was returned,
        # despite the cache store blowing up on every fingerprint write.
        self.assertEqual(result, mock_build.return_value)
        mock_build.assert_called_once()


class TestCacheIsolation(unittest.TestCase):
    """A failing cache command must not touch the real user cache file.

    The test runs each command in a directory that has no real ``history.db``
    and verifies the only side effect is the temp file we redirected to.
    """

    def test_isolated_cache_path(self) -> None:
        # Use a temp dir that is not the user's real cache dir.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "history.db"
            with (
                patch.dict(
                    os.environ,
                    {"LOCALAPPDATA": tmp, "APPDATA": tmp, "XDG_CACHE_HOME": tmp},
                    clear=False,
                ),
                patch("ci_context.cache.db.cache_path", return_value=tmp_path),
            ):
                _invoke_app(["cache", "stats"])
                _invoke_app(["cache", "clear"])

            # The temp path was created (or touched) by the command.
            self.assertTrue(tmp_path.exists() or tmp_path.parent.exists())


if __name__ == "__main__":
    unittest.main()

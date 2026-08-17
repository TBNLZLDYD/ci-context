"""Tests for workflow-run data fetching (runs.py)."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock

from ci_context.github.exceptions import RunNotFoundError
from ci_context.github.runs import get_run, get_workflow_file, list_workflow_runs
from ci_context.models.run import WorkflowRunInfo


def _make_run_mock(**overrides: object) -> MagicMock:
    """Build a PyGithub WorkflowRun stand-in with fixed attributes."""
    defaults: dict[str, object] = {
        "id": 12345,
        "status": "completed",
        "conclusion": "failure",
        "name": "CI Pipeline",
        "head_sha": "0123456789abcdef",
        "event": "push",
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
        "html_url": "https://github.com/owner/repo/actions/runs/12345",
        "run_attempt": 2,
        "run_started_at": datetime(2026, 1, 1, 10, 0, 0),
        "updated_at": datetime(2026, 1, 1, 11, 30, 0),
    }
    defaults.update(overrides)
    run = MagicMock()
    for key, value in defaults.items():
        setattr(run, key, value)
    return run


class TestGetRun(unittest.TestCase):
    """Test get_run field mapping, fallbacks, and error conversion."""

    def _make_client_with_run(self, run: MagicMock) -> MagicMock:
        """Return a mock client whose get_workflow_run yields the given run."""
        client = MagicMock()
        client.get_repo.return_value.get_workflow_run.return_value = run
        return client

    def test_maps_all_fields(self):
        """Every WorkflowRun attribute must map onto WorkflowRunInfo unchanged."""
        client = self._make_client_with_run(_make_run_mock())
        result = get_run(client, "owner/repo", 12345)
        self.assertIsInstance(result, WorkflowRunInfo)
        self.assertEqual(result.id, 12345)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.conclusion, "failure")
        self.assertEqual(result.workflow_name, "CI Pipeline")
        self.assertEqual(result.head_sha, "0123456789abcdef")
        self.assertEqual(result.event, "push")
        self.assertEqual(result.created_at, datetime(2026, 1, 1, 12, 0, 0))
        self.assertEqual(result.url, "https://github.com/owner/repo/actions/runs/12345")
        self.assertEqual(result.attempt, 2)
        # The client must have been asked for the right repo and run id.
        client.get_repo.assert_called_once_with("owner/repo")
        client.get_repo.return_value.get_workflow_run.assert_called_once_with(12345)

    def test_duration_from_updated_minus_started(self):
        """duration_seconds must be the delta between updated_at and run_started_at."""
        client = self._make_client_with_run(_make_run_mock())
        result = get_run(client, "owner/repo", 12345)
        # 10:00 -> 11:30 = 5400 seconds.
        self.assertEqual(result.duration_seconds, 5400.0)

    def test_duration_none_when_timestamps_missing(self):
        """A run that never completed must yield duration_seconds=None, not crash."""
        client = self._make_client_with_run(
            _make_run_mock(run_started_at=None, updated_at=None)
        )
        result = get_run(client, "owner/repo", 12345)
        self.assertIsNone(result.duration_seconds)

    def test_missing_optional_fields_fall_back(self):
        """None status/name/event/attempt must fall back to documented defaults."""
        client = self._make_client_with_run(
            _make_run_mock(status=None, name=None, event=None, run_attempt=None)
        )
        result = get_run(client, "owner/repo", 12345)
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.workflow_name, "Unknown")
        self.assertEqual(result.event, "unknown")
        self.assertEqual(result.attempt, 1)

    def test_missing_created_at_falls_back_to_now(self):
        """None created_at must degrade to a real datetime (datetime.now())."""
        client = self._make_client_with_run(_make_run_mock(created_at=None))
        result = get_run(client, "owner/repo", 12345)
        self.assertIsInstance(result.created_at, datetime)

    def test_missing_head_sha_and_url_fall_back_to_empty(self):
        """None head_sha/html_url must degrade to empty strings, not crash."""
        client = self._make_client_with_run(_make_run_mock(head_sha=None, html_url=None))
        result = get_run(client, "owner/repo", 12345)
        self.assertEqual(result.head_sha, "")
        self.assertEqual(result.url, "")

    def test_get_workflow_run_error_raises_run_not_found(self):
        """An exception from get_workflow_run must surface as RunNotFoundError."""
        client = MagicMock()
        client.get_repo.return_value.get_workflow_run.side_effect = RuntimeError("boom")
        with self.assertRaises(RunNotFoundError) as ctx:
            get_run(client, "owner/repo", 99999)
        # The exception chain must preserve the underlying cause for debugging.
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)

    def test_get_repo_error_propagates_unchanged(self):
        """get_repo failures are repo-level and must NOT be converted to RunNotFoundError."""
        client = MagicMock()
        client.get_repo.side_effect = RuntimeError("repo fetch failed")
        with self.assertRaises(RuntimeError):
            get_run(client, "owner/repo", 12345)


class TestListWorkflowRuns(unittest.TestCase):
    """Test list_workflow_runs workflow scoping, truncation and count clamping."""

    def _make_client_with_runs(
        self, runs: list[MagicMock]
    ) -> tuple[MagicMock, MagicMock]:
        """Return a client whose repo yields the given runs via every listing path."""
        client = MagicMock()
        repo = client.get_repo.return_value
        repo.get_workflow_runs.return_value = runs
        repo.get_workflow.return_value.get_runs.return_value = runs
        return client, repo

    def test_none_workflow_id_uses_repo_wide_listing(self):
        """workflow_id=None must call repo.get_workflow_runs() and NOT scope to a workflow."""
        runs = [_make_run_mock(id=1), _make_run_mock(id=2)]
        client, repo = self._make_client_with_runs(runs)
        result = list_workflow_runs(client, "owner/repo", workflow_id=None, count=2)
        repo.get_workflow_runs.assert_called_once_with()
        repo.get_workflow.assert_not_called()
        self.assertEqual([r.id for r in result], [1, 2])
        self.assertIsInstance(result[0], WorkflowRunInfo)

    def test_int_workflow_id_scopes_to_workflow_by_id(self):
        """An int workflow_id must route through repo.get_workflow(id).get_runs()."""
        runs = [_make_run_mock(id=1), _make_run_mock(id=2)]
        client, repo = self._make_client_with_runs(runs)
        result = list_workflow_runs(client, "owner/repo", workflow_id=42, count=2)
        repo.get_workflow.assert_called_once_with(42)
        repo.get_workflow.return_value.get_runs.assert_called_once_with()
        repo.get_workflow_runs.assert_not_called()
        self.assertEqual([r.id for r in result], [1, 2])

    def test_str_workflow_id_scopes_to_workflow_by_name(self):
        """A str workflow_id must route through repo.get_workflow(name).get_runs()."""
        runs = [_make_run_mock(id=1)]
        client, repo = self._make_client_with_runs(runs)
        result = list_workflow_runs(client, "owner/repo", workflow_id="ci.yml", count=1)
        repo.get_workflow.assert_called_once_with("ci.yml")
        self.assertEqual([r.id for r in result], [1])

    def test_count_truncates_result_list(self):
        """count must cap the returned list at that many entries."""
        runs = [_make_run_mock(id=i) for i in range(1, 6)]
        client, _ = self._make_client_with_runs(runs)
        result = list_workflow_runs(client, "owner/repo", workflow_id=None, count=2)
        self.assertEqual([r.id for r in result], [1, 2])

    def test_count_zero_returns_empty(self):
        """count=0 must return an empty list (islice stops immediately)."""
        runs = [_make_run_mock(id=1)]
        client, _ = self._make_client_with_runs(runs)
        result = list_workflow_runs(client, "owner/repo", workflow_id=None, count=0)
        self.assertEqual(result, [])

    def test_negative_count_clamped_to_empty(self):
        """A negative count must be clamped to 0 and return an empty list."""
        runs = [_make_run_mock(id=1)]
        client, _ = self._make_client_with_runs(runs)
        result = list_workflow_runs(client, "owner/repo", workflow_id=None, count=-5)
        self.assertEqual(result, [])


class TestGetWorkflowFile(unittest.TestCase):
    """Test get_workflow_file basename extraction and graceful None fallbacks."""

    def _make_client(self) -> MagicMock:
        """Return a mock client with an unconfigured get_workflow_run."""
        return MagicMock()

    def test_returns_path_basename(self):
        """A full workflow path must be reduced to its basename (ci.yml)."""
        client = self._make_client()
        client.get_repo.return_value.get_workflow_run.return_value.path = (
            ".github/workflows/ci.yml"
        )
        result = get_workflow_file(client, "owner/repo", 12345)
        self.assertEqual(result, "ci.yml")

    def test_none_path_returns_none(self):
        """A run without a resolvable path must yield None, not crash."""
        client = self._make_client()
        client.get_repo.return_value.get_workflow_run.return_value.path = None
        self.assertIsNone(get_workflow_file(client, "owner/repo", 12345))

    def test_empty_path_returns_none(self):
        """An empty-string path must be treated the same as None."""
        client = self._make_client()
        client.get_repo.return_value.get_workflow_run.return_value.path = ""
        self.assertIsNone(get_workflow_file(client, "owner/repo", 12345))

    def test_get_workflow_run_error_returns_none(self):
        """An exception while fetching the run must degrade to None, not raise."""
        client = self._make_client()
        client.get_repo.return_value.get_workflow_run.side_effect = RuntimeError("boom")
        self.assertIsNone(get_workflow_file(client, "owner/repo", 12345))

    def test_get_repo_error_returns_none(self):
        """Even a repo fetch failure must degrade to None for this best-effort lookup."""
        client = self._make_client()
        client.get_repo.side_effect = RuntimeError("repo fetch failed")
        self.assertIsNone(get_workflow_file(client, "owner/repo", 12345))


if __name__ == "__main__":
    unittest.main()

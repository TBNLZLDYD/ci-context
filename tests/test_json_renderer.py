"""Tests for the JSON renderer (render_json)."""

import json
import unittest
from datetime import UTC, datetime, timedelta, timezone

from ci_context.models.commit import ChangedFile, CommitInfo
from ci_context.models.error import ExtractedError
from ci_context.models.pr import PRInfo, ReviewComment
from ci_context.models.report import FailureReport, HistoryReport, PatternMatch
from ci_context.models.run import WorkflowRunInfo
from ci_context.output.json_renderer import render_json


def _make_run(**overrides: object) -> WorkflowRunInfo:
    """Build a fixed WorkflowRunInfo; tests override only what they assert on."""
    defaults: dict[str, object] = {
        "id": 12345,
        "status": "completed",
        "conclusion": "failure",
        "workflow_name": "CI Pipeline",
        "head_sha": "0123456789abcdef",
        "event": "push",
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
        "url": "https://github.com/owner/repo/actions/runs/12345",
        "attempt": 2,
        "duration_seconds": 3661.0,
    }
    defaults.update(overrides)
    return WorkflowRunInfo(**defaults)  # type: ignore[arg-type]


def _make_full_report() -> FailureReport:
    """Build a report with every context source populated."""
    return FailureReport(
        run=_make_run(),
        errors=[
            ExtractedError(
                error_type="Python Traceback",
                message="ValueError: boom",
                file_location="src/main.py:42",
                confidence="high",
                raw_lines=["RAW_LINE_ONE"],
                occurrence_count=3,
                step_name="build",
            ),
        ],
        commit=CommitInfo(
            sha="0123456789abcdef",
            message="fix the build",
            author="alice",
            changed_files=[
                ChangedFile(path="src/main.py", additions=10, deletions=2),
                ChangedFile(path="src/lib.py", additions=0, deletions=5),
            ],
        ),
        pr=PRInfo(
            number=7,
            title="Fix build",
            author="bob",
            status="open",
            review_state="approved",
            latest_reviews=[
                ReviewComment(author="bob", body="lgtm", created_at="2026-01-01T00:00:00Z"),
            ],
            body_snippet="Fixes the build",
        ),
        history=HistoryReport(
            total_runs_analyzed=20,
            failure_rate="40%",
            recent_failure_rate="60%",
            trend="increasing",
            pattern_matches=[
                PatternMatch(
                    fingerprint="abc123",
                    match_type="exact",
                    occurrence_count=5,
                    first_seen="2026-01-01",
                    last_seen="2026-01-05",
                    related_runs=[1, 2],
                    commit_pattern_hint="Followed dependency updates",
                ),
            ],
        ),
    )


class TestRenderJsonStructure(unittest.TestCase):
    """Verify the top-level JSON shape and that it parses."""

    def test_output_is_valid_json(self):
        """render_json must return text that json.loads can parse."""
        payload = json.loads(render_json(_make_full_report()))
        self.assertIsInstance(payload, dict)

    def test_top_level_keys(self):
        """The payload must expose run/errors/commit/pr/history as top-level keys."""
        payload = json.loads(render_json(_make_full_report()))
        self.assertEqual(set(payload.keys()), {"run", "errors", "commit", "pr", "history"})

    def test_run_fields_serialized(self):
        """Run must carry the full run metadata as flat fields."""
        run = json.loads(render_json(_make_full_report()))["run"]
        self.assertEqual(run["id"], 12345)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["conclusion"], "failure")
        self.assertEqual(run["workflow_name"], "CI Pipeline")
        self.assertEqual(run["head_sha"], "0123456789abcdef")
        self.assertEqual(run["event"], "push")
        self.assertEqual(run["attempt"], 2)
        self.assertEqual(run["duration_seconds"], 3661.0)

    def test_errors_serialized(self):
        """Each error must expose all extracted fields as flat fields."""
        errors = json.loads(render_json(_make_full_report()))["errors"]
        self.assertEqual(len(errors), 1)
        err = errors[0]
        self.assertEqual(err["error_type"], "Python Traceback")
        self.assertEqual(err["message"], "ValueError: boom")
        self.assertEqual(err["file_location"], "src/main.py:42")
        self.assertEqual(err["confidence"], "high")
        self.assertEqual(err["raw_lines"], ["RAW_LINE_ONE"])
        self.assertEqual(err["occurrence_count"], 3)
        self.assertEqual(err["step_name"], "build")


class TestRenderJsonCreatedAt(unittest.TestCase):
    """Verify created_at serializes to UTC 'Z' strings for every tz flavor."""

    def test_naive_datetime_formatted_as_utc_z(self):
        """A naive datetime must be formatted in-place with a Z suffix."""
        run = _make_run(created_at=datetime(2026, 1, 1, 12, 30, 15))
        out = render_json(FailureReport(run=run))
        self.assertEqual(json.loads(out)["run"]["created_at"], "2026-01-01T12:30:15Z")

    def test_aware_utc_datetime_preserved(self):
        """A tz-aware UTC datetime must keep its instant with a Z suffix."""
        run = _make_run(created_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))
        out = render_json(FailureReport(run=run))
        self.assertEqual(json.loads(out)["run"]["created_at"], "2026-01-01T12:00:00Z")

    def test_aware_non_utc_normalized_to_utc(self):
        """A non-UTC aware datetime must be converted to UTC before the Z suffix."""
        # 2026-01-01 08:00 +08:00 is 2026-01-01 00:00 UTC.
        plus_eight = timezone(timedelta(hours=8))
        run = _make_run(created_at=datetime(2026, 1, 1, 8, 0, 0, tzinfo=plus_eight))
        out = render_json(FailureReport(run=run))
        self.assertEqual(json.loads(out)["run"]["created_at"], "2026-01-01T00:00:00Z")


class TestRenderJsonContexts(unittest.TestCase):
    """Verify commit/pr/history serialization and None handling."""

    def test_none_contexts_are_json_null(self):
        """Missing commit/pr/history must serialize to JSON null, not vanish."""
        report = FailureReport(run=_make_run())
        payload = json.loads(render_json(report))
        self.assertIsNone(payload["commit"])
        self.assertIsNone(payload["pr"])
        self.assertIsNone(payload["history"])

    def test_commit_changed_files_serialized(self):
        """changed_files must be a list of {path, additions, deletions} dicts."""
        commit = json.loads(render_json(_make_full_report()))["commit"]
        self.assertEqual(commit["sha"], "0123456789abcdef")
        self.assertEqual(commit["message"], "fix the build")
        self.assertEqual(commit["author"], "alice")
        self.assertEqual(
            commit["changed_files"],
            [
                {"path": "src/main.py", "additions": 10, "deletions": 2},
                {"path": "src/lib.py", "additions": 0, "deletions": 5},
            ],
        )

    def test_pr_fields_serialized(self):
        """PR must carry reviews (author/body/created_at) and the body snippet."""
        pr = json.loads(render_json(_make_full_report()))["pr"]
        self.assertEqual(pr["number"], 7)
        self.assertEqual(pr["title"], "Fix build")
        self.assertEqual(pr["author"], "bob")
        self.assertEqual(pr["status"], "open")
        self.assertEqual(pr["review_state"], "approved")
        self.assertEqual(pr["body_snippet"], "Fixes the build")
        self.assertEqual(
            pr["latest_reviews"],
            [{"author": "bob", "body": "lgtm", "created_at": "2026-01-01T00:00:00Z"}],
        )

    def test_history_fields_serialized(self):
        """History must carry rates, trend, and the pattern-matches array."""
        history = json.loads(render_json(_make_full_report()))["history"]
        self.assertEqual(history["total_runs_analyzed"], 20)
        self.assertEqual(history["failure_rate"], "40%")
        self.assertEqual(history["recent_failure_rate"], "60%")
        self.assertEqual(history["trend"], "increasing")
        match = history["pattern_matches"][0]
        self.assertEqual(match["fingerprint"], "abc123")
        self.assertEqual(match["match_type"], "exact")
        self.assertEqual(match["occurrence_count"], 5)
        self.assertEqual(match["first_seen"], "2026-01-01")
        self.assertEqual(match["last_seen"], "2026-01-05")
        self.assertEqual(match["related_runs"], [1, 2])
        self.assertEqual(match["commit_pattern_hint"], "Followed dependency updates")


class TestRenderJsonUnicode(unittest.TestCase):
    """Verify non-ASCII content stays readable (ensure_ascii=False)."""

    def test_chinese_message_not_escaped(self):
        """Chinese text in an error message must appear literally, not as \\uXXXX."""
        chinese = "构建失败 未找到模块"
        report = FailureReport(
            run=_make_run(),
            errors=[ExtractedError(error_type="ImportError", message=chinese)],
        )
        out = render_json(report)
        # The raw string (not the JSON parse) must contain the literal characters.
        self.assertIn(chinese, out)
        self.assertNotIn("\\u", out)


if __name__ == "__main__":
    unittest.main()

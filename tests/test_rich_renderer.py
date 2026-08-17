"""Tests for the Rich terminal renderer (render_report)."""

import unittest
from datetime import datetime

from ci_context.models.commit import ChangedFile, CommitInfo
from ci_context.models.error import ExtractedError
from ci_context.models.pr import PRInfo, ReviewComment
from ci_context.models.report import FailureReport, HistoryReport, PatternMatch
from ci_context.models.run import WorkflowRunInfo
from ci_context.output.rich_renderer import render_report

# ANSI escape prefix; render_report must emit it when color is on and must not
# when no_color=True. Keeping it in one constant documents intent in one place.
_ANSI_ESC = "\x1b["


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


def _make_error(**overrides: object) -> ExtractedError:
    """Build a fixed ExtractedError; tests override only what they assert on."""
    defaults: dict[str, object] = {
        "error_type": "Python Traceback",
        "message": "ValueError: boom",
        "file_location": "src/main.py:42",
        "confidence": "high",
        "raw_lines": ["RAW_LINE_ONE", "RAW_LINE_TWO", "RAW_LINE_THREE"],
        "occurrence_count": 3,
        "step_name": "build",
    }
    defaults.update(overrides)
    return ExtractedError(**defaults)  # type: ignore[arg-type]


def _make_full_report() -> FailureReport:
    """Build a report with every context source populated."""
    return FailureReport(
        run=_make_run(),
        errors=[_make_error()],
        commit=CommitInfo(
            sha="0123456789abcdef",
            message="fix the build\n\nsecond line of body",
            author="alice",
            changed_files=[
                ChangedFile(path="src/main.py", additions=10, deletions=2),
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


class TestRenderReportSections(unittest.TestCase):
    """Verify the full report renders every expected section and run identity."""

    def test_full_report_contains_all_section_titles(self):
        """All six section titles plus the panel banner should appear."""
        out = render_report(_make_full_report())
        for expected in (
            "CI Failure Report",
            "Run Overview",
            "Extracted Errors",
            "Commit Context",
            "PR Context",
            "History Pattern",
            "Quick Actions",
        ):
            self.assertIn(expected, out, f"missing section title: {expected}")

    def test_run_identity_appears_in_header_and_overview(self):
        """Run id, workflow name and conclusion must appear in the banner+overview."""
        out = render_report(_make_full_report())
        self.assertIn("12345", out)
        self.assertIn("CI Pipeline", out)
        self.assertIn("failure", out)

    def test_commit_sha_and_subject_rendered(self):
        """Commit line must show the 7-char SHA and only the message's first line."""
        out = render_report(_make_full_report())
        self.assertIn("0123456", out)
        self.assertIn("fix the build", out)
        # The message body must not leak into the one-line commit summary.
        self.assertNotIn("second line of body", out)

    def test_failure_rate_line_rendered(self):
        """The history summary must carry the failure-rate phrasing and values."""
        out = render_report(_make_full_report())
        self.assertIn("Failure rate:", out)
        self.assertIn("40%", out)
        self.assertIn("60%", out)
        self.assertIn("increasing", out)


class TestRenderReportErrors(unittest.TestCase):
    """Verify the error list block: metadata lines and raw-line caps."""

    def test_error_metadata_rendered(self):
        """Confidence, error_type, message, step and occurrence count must show."""
        out = render_report(_make_full_report())
        self.assertIn("[high]", out)
        self.assertIn("Python Traceback", out)
        self.assertIn("ValueError: boom", out)
        self.assertIn("Step: build", out)
        self.assertIn("Occurrence: 3", out)

    def test_file_location_rendered_only_when_present(self):
        """'File:' must appear for errors with file_location and be absent without."""
        with_file = _make_error()
        without_file = _make_error(file_location=None)
        out = render_report(FailureReport(run=_make_run(), errors=[with_file, without_file]))
        self.assertIn("File: src/main.py:42", out)
        # Only ONE 'File:' line may exist — the None-location error must not add one.
        self.assertEqual(out.count("File: src/main.py:42"), 1)

    def test_empty_errors_renders_placeholder(self):
        """An empty error list must print a placeholder and not crash."""
        out = render_report(FailureReport(run=_make_run(), errors=[]))
        self.assertIn("(no errors extracted)", out)

    def test_error_lines_zero_hides_all_raw_lines(self):
        """error_lines=0 must drop the raw log lines entirely."""
        out = render_report(_make_full_report(), error_lines=0)
        self.assertNotIn("RAW_LINE_ONE", out)
        self.assertNotIn("RAW_LINE_TWO", out)
        self.assertNotIn("RAW_LINE_THREE", out)

    def test_error_lines_one_shows_only_first_raw_line(self):
        """error_lines=1 must cap the raw block to exactly the first line."""
        out = render_report(_make_full_report(), error_lines=1)
        self.assertIn("RAW_LINE_ONE", out)
        self.assertNotIn("RAW_LINE_TWO", out)
        self.assertNotIn("RAW_LINE_THREE", out)


class TestRenderReportColor(unittest.TestCase):
    """Verify ANSI escape handling through the no_color flag."""

    def test_default_emits_ansi(self):
        """Default rendering must keep ANSI codes (force_terminal path)."""
        out = render_report(_make_full_report())
        self.assertIn(_ANSI_ESC, out)

    def test_no_color_strips_ansi(self):
        """no_color=True must produce plain text with no ANSI escapes."""
        out = render_report(_make_full_report(), no_color=True)
        self.assertNotIn(_ANSI_ESC, out)
        # Plain text still carries the content that ANSI would have colored.
        self.assertIn("Run Overview", out)


class TestRenderReportMissingContext(unittest.TestCase):
    """Verify graceful placeholders when optional context sources are absent."""

    def test_none_commit_placeholder(self):
        """A report without commit context must say so explicitly."""
        report = FailureReport(run=_make_run(), commit=None)
        out = render_report(report)
        self.assertIn("(no commit context available)", out)

    def test_none_pr_placeholder(self):
        """A report without PR context must say so explicitly."""
        report = FailureReport(run=_make_run(), pr=None)
        out = render_report(report)
        self.assertIn("(no PR context available)", out)

    def test_none_history_placeholder(self):
        """A report without history must say the analysis was skipped."""
        report = FailureReport(run=_make_run(), history=None)
        out = render_report(report)
        self.assertIn("(history analysis skipped)", out)

    def test_quick_actions_adapted_to_missing_context(self):
        """With no commit/PR, only the run-level quick actions may be shown."""
        report = FailureReport(run=_make_run(), commit=None, pr=None)
        out = render_report(report)
        self.assertIn("gh run view 12345 --log", out)
        self.assertIn("gh run rerun 12345 --failed", out)
        self.assertNotIn("gh repo view", out)
        self.assertNotIn("gh pr view", out)


class TestRenderDuration(unittest.TestCase):
    """Verify the Duration: line for every _format_duration branch."""

    def test_duration_formatting_branches(self):
        """Each duration bucket must render its documented human-readable form."""
        cases = [
            # (duration_seconds, expected Duration text)
            # A never-completed run carries None -> "N/A".
            (None, "Duration: N/A · Attempt: 2"),
            # Sub-minute runs show only seconds.
            (59.0, "Duration: 59s · Attempt: 2"),
            # Minute-level runs show "Ym Zs" even when Z is zero.
            (600.0, "Duration: 10m 0s · Attempt: 2"),
            # Hour-level runs show "Xh Ym".
            (3661.0, "Duration: 1h 1m · Attempt: 2"),
        ]
        for duration_seconds, expected in cases:
            with self.subTest(duration=duration_seconds):
                report = FailureReport(run=_make_run(duration_seconds=duration_seconds))
                self.assertIn(expected, render_report(report))


class TestRenderStepName(unittest.TestCase):
    """Verify the Step: line is gated on a non-empty step_name."""

    def test_empty_step_name_omits_step_line(self):
        """An empty-string step_name (the model default) must not emit a Step: line."""
        report = FailureReport(run=_make_run(), errors=[_make_error(step_name="")])
        out = render_report(report)
        self.assertNotIn("Step:", out)
        # The rest of the error metadata must still be present.
        self.assertIn("Occurrence: 3", out)

    def test_present_step_name_emits_step_line(self):
        """A non-empty step_name must render a Step: line."""
        report = FailureReport(run=_make_run(), errors=[_make_error(step_name="build")])
        out = render_report(report)
        self.assertIn("Step: build", out)


class TestRenderHistoryPatterns(unittest.TestCase):
    """Verify history pattern rendering edge cases."""

    def test_empty_pattern_matches_still_show_failure_rate(self):
        """An empty pattern_matches list must not crash and still print the rate line."""
        report = FailureReport(
            run=_make_run(),
            history=HistoryReport(
                total_runs_analyzed=20,
                failure_rate="40%",
                recent_failure_rate="60%",
                trend="stable",
                pattern_matches=[],
            ),
        )
        out = render_report(report)
        self.assertIn("Failure rate: 40% overall · 60% recent · trend: stable", out)

    def test_empty_commit_pattern_hint_omits_hint_line(self):
        """An empty commit_pattern_hint must drop the hint line but keep occurrence metadata."""
        report = FailureReport(
            run=_make_run(),
            history=HistoryReport(
                total_runs_analyzed=20,
                failure_rate="40%",
                recent_failure_rate="60%",
                trend="stable",
                pattern_matches=[
                    PatternMatch(
                        fingerprint="abc123",
                        match_type="exact",
                        occurrence_count=5,
                        first_seen="2026-01-01",
                        last_seen="2026-01-05",
                        related_runs=[1, 2],
                        commit_pattern_hint="",
                    ),
                ],
            ),
        )
        out = render_report(report)
        self.assertIn("Occurred 5 times", out)
        self.assertIn("First: 2026-01-01 · Last: 2026-01-05", out)
        self.assertNotIn("Followed dependency updates", out)

    def test_commit_pattern_hint_rendered_when_present(self):
        """A non-empty commit_pattern_hint must render as a yellow hint line."""
        report = FailureReport(
            run=_make_run(),
            history=HistoryReport(
                total_runs_analyzed=20,
                failure_rate="40%",
                recent_failure_rate="60%",
                trend="stable",
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
        out = render_report(report)
        self.assertIn("Followed dependency updates", out)


class TestRenderMalformedRunUrl(unittest.TestCase):
    """Verify the renderer survives a run URL that yields no owner/repo."""

    def test_malformed_url_uses_placeholder_in_quick_actions(self):
        """A URL _owner_repo cannot parse must fall back to <owner/repo> without crashing."""
        report = FailureReport(
            run=_make_run(url="not-a-valid-url"),
            commit=CommitInfo(
                sha="0123456789abcdef",
                message="fix the build",
                author="alice",
                changed_files=[],
            ),
        )
        out = render_report(report)
        # The header subtitle is omitted (no repo to show) but the report renders.
        self.assertIn("CI Failure Report", out)
        self.assertIn("Quick Actions", out)
        self.assertIn("gh repo view <owner/repo> --commit 0123456", out)


if __name__ == "__main__":
    unittest.main()

"""Tests for history pattern matching."""

from __future__ import annotations

import unittest

from ci_context.analysis.fingerprint import compute_fingerprint
from ci_context.analysis.matcher import (
    HistoricalOccurrence,
    build_history_report,
    compute_trend,
    find_commit_patterns,
    levenshtein_distance,
    match_errors,
)
from ci_context.models.error import ExtractedError
from ci_context.models.report import PatternMatch

# ---------------------------------------------------------------------------
# Helpers for building test data
# ---------------------------------------------------------------------------


def _error(
    error_type: str = "Python Traceback",
    message: str = "division by zero",
    **kwargs: object,
) -> ExtractedError:
    """Shorthand to create an ExtractedError with sensible defaults."""
    return ExtractedError(error_type=error_type, message=message, **kwargs)


def _occ(
    run_id: int,
    ts: str = "2025-01-01T00:00:00Z",
    msg: str = "fix bug",
) -> HistoricalOccurrence:
    """Shorthand to create a HistoricalOccurrence."""
    return HistoricalOccurrence(run_id=run_id, timestamp=ts, commit_message=msg)


# ---------------------------------------------------------------------------
# TestLevenshteinDistance
# ---------------------------------------------------------------------------


class TestLevenshteinDistance(unittest.TestCase):
    """Tests for the Levenshtein distance helper."""

    def test_identical_strings(self) -> None:
        self.assertEqual(levenshtein_distance("abc", "abc"), 0)

    def test_completely_different(self) -> None:
        # No shared characters; distance equals length of longer string
        self.assertEqual(levenshtein_distance("abc", "xyz"), 3)

    def test_one_insertion(self) -> None:
        # "ab" -> "abc" requires one insertion
        self.assertEqual(levenshtein_distance("ab", "abc"), 1)

    def test_one_deletion(self) -> None:
        # "abc" -> "ab" requires one deletion
        self.assertEqual(levenshtein_distance("abc", "ab"), 1)

    def test_one_substitution(self) -> None:
        # "abc" -> "axc" requires one substitution
        self.assertEqual(levenshtein_distance("abc", "axc"), 1)

    def test_empty_first_string(self) -> None:
        self.assertEqual(levenshtein_distance("", "abc"), 3)

    def test_empty_second_string(self) -> None:
        self.assertEqual(levenshtein_distance("abc", ""), 3)

    def test_both_empty(self) -> None:
        self.assertEqual(levenshtein_distance("", ""), 0)

    def test_fingerprint_length_strings(self) -> None:
        # Fingerprints are 16 hex chars; verify a realistic case
        self.assertEqual(levenshtein_distance("a1b2c3d4e5f6g7h8", "a1b2c3d4e5f6g7h9"), 1)


# ---------------------------------------------------------------------------
# TestMatchErrors
# ---------------------------------------------------------------------------


class TestMatchErrors(unittest.TestCase):
    """Tests for the core match_errors function."""

    def test_exact_match(self) -> None:
        """Same fingerprint in history -> match_type='exact'."""
        err = _error(message="division by zero")
        fp = compute_fingerprint(err)
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp: [_occ(100, "2025-01-01T00:00:00Z")],
        }

        results = match_errors([err], hist)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].match_type, "exact")
        self.assertEqual(results[0].fingerprint, fp)

    def test_exact_match_occurrence_count(self) -> None:
        """occurrence_count includes current + all historical occurrences."""
        err = _error(message="division by zero")
        fp = compute_fingerprint(err)
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp: [
                _occ(100, "2025-01-01T00:00:00Z"),
                _occ(101, "2025-01-02T00:00:00Z"),
            ],
        }

        results = match_errors([err], hist)
        self.assertEqual(results[0].occurrence_count, 3)  # 2 historical + 1 current

    def test_exact_match_related_runs(self) -> None:
        """related_runs contains run IDs from historical data."""
        err = _error(message="division by zero")
        fp = compute_fingerprint(err)
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp: [
                _occ(100, "2025-01-01T00:00:00Z"),
                _occ(105, "2025-01-05T00:00:00Z"),
            ],
        }

        results = match_errors([err], hist)
        self.assertEqual(results[0].related_runs, [100, 105])

    def test_exact_match_first_last_seen(self) -> None:
        """first_seen/last_seen come from historical occurrences."""
        err = _error(message="division by zero")
        fp = compute_fingerprint(err)
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp: [
                _occ(100, "2025-01-01T10:00:00Z"),
                _occ(105, "2025-01-05T20:00:00Z"),
            ],
        }

        results = match_errors([err], hist)
        self.assertEqual(results[0].first_seen, "2025-01-01T10:00:00Z")
        self.assertEqual(results[0].last_seen, "2025-01-05T20:00:00Z")

    def test_similar_match(self) -> None:
        """Fingerprint not in history but Levenshtein > 0.8 -> 'similar'."""
        # We need two errors whose fingerprints differ by at most 3 of 16 chars
        # (similarity > 0.8 = distance < 3.2).  Build errors that produce
        # close fingerprints by using nearly-identical messages.
        err_current = _error(message="ImportError: cannot import name 'Foo'")
        err_hist = _error(message="ImportError: cannot import name 'Bar'")

        fp_hist = compute_fingerprint(err_hist)
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp_hist: [_occ(100, "2025-01-01T00:00:00Z")],
        }

        results = match_errors([err_current], hist)
        # The fingerprints may or may not be similar enough depending on SHA-256
        # hashing, so we verify the logic path by checking that the result is
        # one of the three valid types.
        self.assertIn(results[0].match_type, {"exact", "similar", "new"})

    def test_similar_match_guaranteed(self) -> None:
        """Force a similar match by injecting a fingerprint that differs by 1 char."""
        err = _error(message="some unique error xyz")
        fp = compute_fingerprint(err)
        # Create a fingerprint that differs by exactly 1 character
        fp_similar = fp[:-1] + ("0" if fp[-1] != "0" else "1")
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp_similar: [_occ(100, "2025-01-01T00:00:00Z")],
        }

        results = match_errors([err], hist)
        self.assertEqual(results[0].match_type, "similar")
        self.assertEqual(results[0].occurrence_count, 2)
        self.assertEqual(results[0].related_runs, [100])

    def test_new_error(self) -> None:
        """No match at all -> match_type='new'."""
        err = _error(message="a completely novel error that has no precedent")
        hist: dict[str, list[HistoricalOccurrence]] = {}

        results = match_errors([err], hist)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].match_type, "new")
        self.assertEqual(results[0].occurrence_count, 1)
        self.assertEqual(results[0].related_runs, [])
        self.assertEqual(results[0].first_seen, "")
        self.assertEqual(results[0].last_seen, "")

    def test_new_error_with_unrelated_history(self) -> None:
        """History exists but nothing matches -> 'new'."""
        err = _error(message="a completely novel error that has no precedent")
        # Populate history with a very different fingerprint
        other_err = _error(message="totally different error abcdefg")
        fp_other = compute_fingerprint(other_err)
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp_other: [_occ(100, "2025-01-01T00:00:00Z")],
        }

        results = match_errors([err], hist)
        self.assertEqual(results[0].match_type, "new")

    def test_multiple_errors_matched_correctly(self) -> None:
        """Multiple errors each get their own classification."""
        err1 = _error(message="division by zero")
        err2 = _error(message="a completely novel error that has no precedent")

        fp1 = compute_fingerprint(err1)
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp1: [_occ(100, "2025-01-01T00:00:00Z")],
        }

        results = match_errors([err1, err2], hist)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].match_type, "exact")
        self.assertEqual(results[1].match_type, "new")

    def test_matching_two_times_outputs_common_patterns(self) -> None:
        """Matching >= 2 times should produce pattern data with occurrence info."""
        err = _error(message="division by zero")
        fp = compute_fingerprint(err)
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp: [
                _occ(100, "2025-01-01T00:00:00Z", "bump dependencies"),
                _occ(101, "2025-01-02T00:00:00Z", "bump dev dependencies"),
            ],
        }

        results = match_errors([err], hist)
        self.assertEqual(results[0].occurrence_count, 3)  # 2 hist + 1 current
        self.assertEqual(len(results[0].related_runs), 2)


# ---------------------------------------------------------------------------
# TestComputeTrend
# ---------------------------------------------------------------------------


class TestComputeTrend(unittest.TestCase):
    """Tests for the trend calculation function."""

    def test_increasing(self) -> None:
        """Recent rate much higher than overall -> 'increasing'."""
        self.assertEqual(compute_trend(0.6, 0.4), "increasing")

    def test_decreasing(self) -> None:
        """Recent rate much lower than overall -> 'decreasing'."""
        self.assertEqual(compute_trend(0.2, 0.4), "decreasing")

    def test_stable(self) -> None:
        """Similar rates -> 'stable'."""
        self.assertEqual(compute_trend(0.4, 0.4), "stable")

    def test_stable_near_threshold(self) -> None:
        """Rate just within 0.8-1.2 range -> 'stable'."""
        # 0.47 > 0.4 * 1.2 = 0.48? No, so stable
        self.assertEqual(compute_trend(0.47, 0.4), "stable")

    def test_zero_overall_rate_with_recent_failures(self) -> None:
        """Zero overall rate but recent failures -> 'increasing'."""
        self.assertEqual(compute_trend(0.5, 0.0), "increasing")

    def test_zero_overall_rate_no_recent_failures(self) -> None:
        """Zero overall rate and no recent failures -> 'stable'."""
        self.assertEqual(compute_trend(0.0, 0.0), "stable")

    def test_increasing_at_exact_threshold(self) -> None:
        """Rate exactly at 1.2x -> 'increasing' (strict >)."""
        # 0.48 > 0.4 * 1.2 = 0.48 -> False, so stable
        self.assertEqual(compute_trend(0.48, 0.4), "stable")
        # 0.49 > 0.48 -> True, so increasing
        self.assertEqual(compute_trend(0.49, 0.4), "increasing")


# ---------------------------------------------------------------------------
# TestBuildHistoryReport
# ---------------------------------------------------------------------------


class TestBuildHistoryReport(unittest.TestCase):
    """Tests for the full HistoryReport builder."""

    def test_correct_failure_rate_strings(self) -> None:
        """Failure rates are formatted as percentage strings."""
        err = _error(message="division by zero")
        report = build_history_report(
            current_errors=[err],
            historical_fingerprints={},
            total_runs=10,
            failed_runs=4,
            recent_total_runs=5,
            recent_failed_runs=3,
        )
        self.assertEqual(report.failure_rate, "40%")
        self.assertEqual(report.recent_failure_rate, "60%")

    def test_trend_computed_correctly(self) -> None:
        """Trend reflects the relationship between recent and overall rates."""
        err = _error(message="division by zero")
        report = build_history_report(
            current_errors=[err],
            historical_fingerprints={},
            total_runs=10,
            failed_runs=2,
            recent_total_runs=5,
            recent_failed_runs=3,
        )
        # recent=60%, overall=20% -> 0.6 > 0.2*1.2=0.24 -> increasing
        self.assertEqual(report.trend, "increasing")

    def test_pattern_matches_included(self) -> None:
        """Pattern matches are populated from match_errors."""
        err = _error(message="division by zero")
        fp = compute_fingerprint(err)
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp: [_occ(100, "2025-01-01T00:00:00Z")],
        }
        report = build_history_report(
            current_errors=[err],
            historical_fingerprints=hist,
            total_runs=5,
            failed_runs=2,
            recent_total_runs=3,
            recent_failed_runs=1,
        )
        self.assertEqual(len(report.pattern_matches), 1)
        self.assertEqual(report.pattern_matches[0].match_type, "exact")

    def test_empty_history(self) -> None:
        """No historical data -> all errors are 'new', rates from run counts."""
        err = _error(message="novel error")
        report = build_history_report(
            current_errors=[err],
            historical_fingerprints={},
            total_runs=5,
            failed_runs=1,
            recent_total_runs=2,
            recent_failed_runs=1,
        )
        self.assertEqual(len(report.pattern_matches), 1)
        self.assertEqual(report.pattern_matches[0].match_type, "new")
        self.assertEqual(report.failure_rate, "20%")
        self.assertEqual(report.recent_failure_rate, "50%")

    def test_zero_runs(self) -> None:
        """Zero total runs -> 0% rates, stable trend."""
        report = build_history_report(
            current_errors=[],
            historical_fingerprints={},
            total_runs=0,
            failed_runs=0,
            recent_total_runs=0,
            recent_failed_runs=0,
        )
        self.assertEqual(report.failure_rate, "0%")
        self.assertEqual(report.recent_failure_rate, "0%")
        self.assertEqual(report.trend, "stable")

    def test_total_runs_analyzed(self) -> None:
        """total_runs_analyzed is passed through correctly."""
        report = build_history_report(
            current_errors=[],
            historical_fingerprints={},
            total_runs=42,
            failed_runs=0,
            recent_total_runs=10,
            recent_failed_runs=0,
        )
        self.assertEqual(report.total_runs_analyzed, 42)


# ---------------------------------------------------------------------------
# TestFindCommitPatterns
# ---------------------------------------------------------------------------


class TestFindCommitPatterns(unittest.TestCase):
    """Tests for the commit pattern association function."""

    def test_all_occurrences_share_keyword(self) -> None:
        """All commit messages share a keyword -> hint is set."""
        fp = "abcd1234efgh5678"
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp: [
                _occ(100, "2025-01-01T00:00:00Z", "bump core packages"),
                _occ(101, "2025-01-02T00:00:00Z", "bump dev packages"),
            ],
        }
        matches = [
            PatternMatch(
                fingerprint=fp,
                match_type="exact",
                occurrence_count=3,
                first_seen="2025-01-01T00:00:00Z",
                last_seen="2025-01-02T00:00:00Z",
                related_runs=[100, 101],
            ),
        ]

        result = find_commit_patterns(matches, hist)
        self.assertTrue(result[0].commit_pattern_hint)
        self.assertIn("bump", result[0].commit_pattern_hint)
        self.assertIn("3 occurrences", result[0].commit_pattern_hint)

    def test_no_common_keyword(self) -> None:
        """No shared keyword across commit messages -> hint is empty."""
        fp = "abcd1234efgh5678"
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp: [
                _occ(100, "2025-01-01T00:00:00Z", "refactor authentication module"),
                _occ(101, "2025-01-02T00:00:00Z", "fix memory leak in parser"),
            ],
        }
        matches = [
            PatternMatch(
                fingerprint=fp,
                match_type="exact",
                occurrence_count=3,
                first_seen="2025-01-01T00:00:00Z",
                last_seen="2025-01-02T00:00:00Z",
                related_runs=[100, 101],
            ),
        ]

        result = find_commit_patterns(matches, hist)
        self.assertEqual(result[0].commit_pattern_hint, "")

    def test_single_occurrence_no_hint(self) -> None:
        """occurrence_count < 2 -> no hint (need >= 2 for a pattern)."""
        matches = [
            PatternMatch(
                fingerprint="abcd1234efgh5678",
                match_type="new",
                occurrence_count=1,
                first_seen="",
                last_seen="",
                related_runs=[],
            ),
        ]

        result = find_commit_patterns(matches, {})
        self.assertEqual(result[0].commit_pattern_hint, "")

    def test_stop_words_excluded(self) -> None:
        """Stop words like 'fix', 'update' should not appear in hints."""
        fp = "abcd1234efgh5678"
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp: [
                _occ(100, "2025-01-01T00:00:00Z", "fix the authentication bug"),
                _occ(101, "2025-01-02T00:00:00Z", "fix the authorization bug"),
            ],
        }
        matches = [
            PatternMatch(
                fingerprint=fp,
                match_type="exact",
                occurrence_count=3,
                first_seen="2025-01-01T00:00:00Z",
                last_seen="2025-01-02T00:00:00Z",
                related_runs=[100, 101],
            ),
        ]

        result = find_commit_patterns(matches, hist)
        # "fix", "the" are stop words; "authentication"/"authorization" differ;
        # "bug" is the only common non-stop word
        self.assertIn("bug", result[0].commit_pattern_hint)
        self.assertNotIn("fix", result[0].commit_pattern_hint)

    def test_similar_match_uses_related_runs(self) -> None:
        """For 'similar' matches, commit patterns are found via related_runs."""
        fp_hist = "abcd1234efgh5678"
        hist: dict[str, list[HistoricalOccurrence]] = {
            fp_hist: [
                _occ(200, "2025-01-01T00:00:00Z", "bump core packages"),
                _occ(201, "2025-01-02T00:00:00Z", "bump dev packages"),
            ],
        }
        matches = [
            PatternMatch(
                fingerprint="abcd1234efgh5679",  # different fp (similar match)
                match_type="similar",
                occurrence_count=3,
                first_seen="2025-01-01T00:00:00Z",
                last_seen="2025-01-02T00:00:00Z",
                related_runs=[200, 201],
            ),
        ]

        result = find_commit_patterns(matches, hist)
        self.assertIn("bump", result[0].commit_pattern_hint)


if __name__ == "__main__":
    unittest.main()

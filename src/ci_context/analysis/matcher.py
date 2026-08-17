"""History pattern matching — find recurring errors across workflow runs.

Given a set of current errors and a historical fingerprint database, classify
each error as [EXACT] (seen before with identical fingerprint), [SIMILAR]
(fingerprint close enough via Levenshtein similarity > 0.8), or [NEW] (never
seen).  Also computes failure-rate trends and commit-message pattern hints.
"""

from __future__ import annotations

from dataclasses import dataclass

from ci_context.analysis.fingerprint import compute_fingerprint
from ci_context.models.error import ExtractedError
from ci_context.models.report import HistoryReport, PatternMatch

# ---------------------------------------------------------------------------
# Historical occurrence record — internal helper, not a top-level model
# ---------------------------------------------------------------------------


@dataclass
class HistoricalOccurrence:
    """A single past occurrence of a fingerprint in a workflow run."""

    run_id: int
    timestamp: str  # ISO-8601
    commit_message: str


# ---------------------------------------------------------------------------
# Levenshtein distance — classic DP, no external dependency
# ---------------------------------------------------------------------------


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute the Levenshtein (edit) distance between two strings.

    Uses the Wagner-Fischer DP algorithm.  Fingerprints are short (16 chars)
    so the O(mn) cost is negligible.
    """
    m, n = len(s1), len(s2)
    # Fast path: either string empty
    if m == 0:
        return n
    if n == 0:
        return m

    # Single-row optimisation: we only need the previous row
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        curr: list[int] = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,   # insertion
                prev[j] + 1,       # deletion
                prev[j - 1] + cost,  # substitution
            )
        prev = curr

    return prev[n]


def _levenshtein_similarity(s1: str, s2: str) -> float:
    """Normalised Levenshtein similarity in [0.0, 1.0].

    1.0 = identical, 0.0 = completely different.
    """
    if not s1 and not s2:
        return 1.0
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    return 1.0 - levenshtein_distance(s1, s2) / max_len


# ---------------------------------------------------------------------------
# Core matching logic
# ---------------------------------------------------------------------------


def match_errors(
    current_errors: list[ExtractedError],
    historical_fingerprints: dict[str, list[HistoricalOccurrence]],
) -> list[PatternMatch]:
    """Classify each current error against historical fingerprint data.

    For every error we compute its fingerprint, then:
      - Exact match in history  -> match_type = "exact"
      - No exact match, but Levenshtein similarity > 0.8 with some historical
        fingerprint -> match_type = "similar" (best match wins)
      - Otherwise -> match_type = "new"

    Returns one PatternMatch per input error, preserving input order.
    """
    results: list[PatternMatch] = []

    # Pre-collect all historical fingerprint keys for similarity scanning
    all_hist_fps = list(historical_fingerprints.keys())

    for error in current_errors:
        fp = compute_fingerprint(error)

        # --- Exact match ---------------------------------------------------
        if fp in historical_fingerprints:
            occs = historical_fingerprints[fp]
            results.append(
                PatternMatch(
                    fingerprint=fp,
                    match_type="exact",
                    occurrence_count=len(occs) + 1,  # +1 for current occurrence
                    first_seen=occs[0].timestamp,
                    last_seen=occs[-1].timestamp,
                    related_runs=[o.run_id for o in occs],
                )
            )
            continue

        # --- Similar match (Levenshtein) -----------------------------------
        best_sim = 0.0
        best_fp = ""
        for hist_fp in all_hist_fps:
            sim = _levenshtein_similarity(fp, hist_fp)
            if sim > best_sim:
                best_sim = sim
                best_fp = hist_fp

        if best_sim > 0.8 and best_fp:
            occs = historical_fingerprints[best_fp]
            results.append(
                PatternMatch(
                    fingerprint=fp,
                    match_type="similar",
                    occurrence_count=len(occs) + 1,
                    first_seen=occs[0].timestamp,
                    last_seen=occs[-1].timestamp,
                    related_runs=[o.run_id for o in occs],
                )
            )
            continue

        # --- New error -----------------------------------------------------
        results.append(
            PatternMatch(
                fingerprint=fp,
                match_type="new",
                occurrence_count=1,
                first_seen="",
                last_seen="",
                related_runs=[],
            )
        )

    return results


# ---------------------------------------------------------------------------
# Trend calculation
# ---------------------------------------------------------------------------

# Thresholds: recent rate must differ from overall by at least 20% to be
# considered a trend rather than noise.
_TREND_INCREASE_FACTOR = 1.2
_TREND_DECREASE_FACTOR = 0.8


def compute_trend(recent_failure_rate: float, overall_failure_rate: float) -> str:
    """Classify the failure-rate trend.

    Args:
        recent_failure_rate: Failure rate in the recent window (0.0-1.0).
        overall_failure_rate: Failure rate across all history (0.0-1.0).

    Returns:
        "increasing", "decreasing", or "stable".
    """
    if overall_failure_rate == 0.0:
        # Any recent failures when there were none before is an increase
        return "increasing" if recent_failure_rate > 0.0 else "stable"

    if recent_failure_rate > overall_failure_rate * _TREND_INCREASE_FACTOR:
        return "increasing"
    if recent_failure_rate < overall_failure_rate * _TREND_DECREASE_FACTOR:
        return "decreasing"
    return "stable"


# ---------------------------------------------------------------------------
# Commit pattern association
# ---------------------------------------------------------------------------

# Stop words that appear in almost every commit message and carry no
# diagnostic value for pattern hints.
_STOP_WORDS = frozenset({
    "the", "a", "an", "to", "of", "in", "for", "on", "with", "at", "by",
    "from", "and", "or", "is", "it", "this", "that", "be", "are", "was",
    "were", "has", "have", "had", "do", "does", "did", "not", "no", "but",
    "if", "as", "so", "up", "out", "can", "will", "just", "into", "also",
    "fix", "update", "add", "remove", "change", "merge", "pull", "request",
    "commit", "branch", "main", "master", "head",
})


def find_commit_patterns(
    matches: list[PatternMatch],
    historical_fingerprints: dict[str, list[HistoricalOccurrence]],
) -> list[PatternMatch]:
    """Enrich PatternMatch entries with commit_pattern_hint.

    For matches that occurred >= 2 times (i.e. have historical data), inspect
    the commit messages of all occurrences.  If every message shares a common
    non-stop-word, that word becomes the hint.  The hint is formatted as
    "All N occurrences followed <keyword> commits".
    """
    updated: list[PatternMatch] = []

    for match in matches:
        # Only look for patterns when there are multiple occurrences
        if match.occurrence_count < 2:
            updated.append(match)
            continue

        # Gather commit messages from the best-matching historical fingerprint.
        # For "exact" matches the fingerprint key is match.fingerprint itself;
        # for "similar" matches the related_runs already point to the right
        # historical entries, so we scan all fingerprints for those run_ids.
        related_run_ids = set(match.related_runs)
        commit_messages: list[str] = []

        if match.match_type == "exact" and match.fingerprint in historical_fingerprints:
            commit_messages = [
                o.commit_message
                for o in historical_fingerprints[match.fingerprint]
            ]
        else:
            # Scan all historical entries for matching run IDs
            for occs in historical_fingerprints.values():
                for o in occs:
                    if o.run_id in related_run_ids:
                        commit_messages.append(o.commit_message)

        if not commit_messages:
            updated.append(match)
            continue

        # Tokenise and lowercase each message, filtering stop words
        word_sets: list[set[str]] = []
        for msg in commit_messages:
            words = {
                w.lower()
                for w in msg.split()
                if w.lower() not in _STOP_WORDS and len(w) > 1
            }
            word_sets.append(words)

        if not word_sets:
            updated.append(match)
            continue

        # Intersection of all word sets = words present in EVERY message
        common = word_sets[0]
        for ws in word_sets[1:]:
            common &= ws

        hint = ""
        if common:
            # Pick the rarest (most specific) common word; break ties
            # alphabetically for deterministic output across hash-randomised runs.
            keyword = min(
                common,
                key=lambda w: (sum(1 for ws in word_sets if w in ws), w),
            )
            hint = (
                f"All {match.occurrence_count} occurrences "
                f"followed {keyword} commits"
            )

        updated.append(
            PatternMatch(
                fingerprint=match.fingerprint,
                match_type=match.match_type,
                occurrence_count=match.occurrence_count,
                first_seen=match.first_seen,
                last_seen=match.last_seen,
                related_runs=match.related_runs,
                commit_pattern_hint=hint,
            )
        )

    return updated


# ---------------------------------------------------------------------------
# HistoryReport builder — top-level entry point
# ---------------------------------------------------------------------------


def build_history_report(
    current_errors: list[ExtractedError],
    historical_fingerprints: dict[str, list[HistoricalOccurrence]],
    total_runs: int,
    failed_runs: int,
    recent_total_runs: int,
    recent_failed_runs: int,
) -> HistoryReport:
    """Build a complete HistoryReport from current errors and historical data.

    Computes failure rates, trend, pattern matches (with commit hints), and
    assembles the final report.
    """
    # Failure rates as percentage strings
    overall_rate = failed_runs / total_runs if total_runs > 0 else 0.0
    recent_rate = (
        recent_failed_runs / recent_total_runs if recent_total_runs > 0 else 0.0
    )

    # round rather than int: truncation would show 66% for 2/3 failures, which
    # understates the true 67% and surprises readers.
    failure_rate_pct = f"{round(overall_rate * 100)}%"
    recent_failure_rate_pct = f"{round(recent_rate * 100)}%"

    trend = compute_trend(recent_rate, overall_rate)

    # Match errors and enrich with commit patterns
    matches = match_errors(current_errors, historical_fingerprints)
    matches = find_commit_patterns(matches, historical_fingerprints)

    return HistoryReport(
        total_runs_analyzed=total_runs,
        failure_rate=failure_rate_pct,
        recent_failure_rate=recent_failure_rate_pct,
        trend=trend,
        pattern_matches=matches,
    )

"""Composite report data model — aggregates all context into one structure."""

from __future__ import annotations

from dataclasses import dataclass, field

from ci_context.models.run import WorkflowRunInfo
from ci_context.models.error import ExtractedError
from ci_context.models.commit import CommitInfo
from ci_context.models.pr import PRInfo


@dataclass
class PatternMatch:
    """A single history pattern match result."""

    fingerprint: str
    match_type: str  # "exact" | "similar" | "new"
    occurrence_count: int
    first_seen: str
    last_seen: str
    related_runs: list[int] = field(default_factory=list)
    commit_pattern_hint: str = ""  # e.g. "All 3 occurrences followed dependency updates"


@dataclass
class HistoryReport:
    """History pattern matching results for a workflow."""

    total_runs_analyzed: int
    failure_rate: str  # "40%"
    recent_failure_rate: str  # "60%"
    trend: str  # "increasing" | "stable" | "decreasing"
    pattern_matches: list[PatternMatch] = field(default_factory=list)


@dataclass
class FailureReport:
    """
    The complete failure diagnosis report.

    This is the top-level data structure that all context sources feed into
    and that renderers consume to produce terminal/JSON output.
    """

    run: WorkflowRunInfo
    errors: list[ExtractedError] = field(default_factory=list)
    commit: CommitInfo | None = None
    pr: PRInfo | None = None
    history: HistoryReport | None = None

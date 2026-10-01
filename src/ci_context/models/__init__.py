"""Pure dataclasses shared across the pipeline.

Run metadata, extracted errors, commit/PR context, and the composite report
live in one module: the classes are small and evolve together, so per-domain
files only added navigation cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class WorkflowRunInfo:
    """Structured representation of a GitHub Actions workflow run."""

    id: int
    status: str  # "queued" | "in_progress" | "completed"
    conclusion: str | None  # "success" | "failure" | "cancelled" | None
    workflow_name: str
    head_sha: str
    event: str  # "push" | "pull_request" | ...
    created_at: datetime
    url: str
    attempt: int = 1
    duration_seconds: float | None = None


@dataclass
class ExtractedError:
    """
    A single error extracted from CI log text.

    Produced by the error extraction engine (analysis/extractor.py) and consumed
    by the renderers and the fingerprint/matcher modules.
    """

    error_type: str  # "Python Traceback", "npm Error", "Go panic", etc.
    message: str  # Core error message
    file_location: str | None = None  # "src/main.py:42" or None
    confidence: str = "medium"  # "high" | "medium" | "low"
    raw_lines: list[str] = field(default_factory=list)  # Original log lines (up to 5)
    occurrence_count: int = 1  # How many times this error appeared in the run
    step_name: str = ""  # Which step produced this error


@dataclass
class ChangedFile:
    """A single file changed in a commit."""

    path: str
    additions: int
    deletions: int


@dataclass
class CommitInfo:
    """Structured representation of a git commit relevant to a CI run."""

    sha: str
    message: str
    author: str
    changed_files: list[ChangedFile] = field(default_factory=list)


@dataclass
class ReviewComment:
    """A single PR review comment."""

    author: str
    body: str  # Truncated to 200 chars
    created_at: str


@dataclass
class PRInfo:
    """Structured representation of a pull request that triggered a CI run."""

    number: int
    title: str
    author: str
    status: str  # "open" | "merged" | "closed"
    review_state: str  # "approved" | "changes_requested" | "pending"
    latest_reviews: list[ReviewComment] = field(default_factory=list)
    body_snippet: str = ""  # Truncated to 500 chars


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


__all__ = [
    "ChangedFile",
    "CommitInfo",
    "ExtractedError",
    "FailureReport",
    "HistoryReport",
    "PRInfo",
    "PatternMatch",
    "ReviewComment",
    "WorkflowRunInfo",
]

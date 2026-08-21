"""JSON renderer — produce machine-readable JSON output."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from ci_context.models.commit import ChangedFile, CommitInfo
from ci_context.models.error import ExtractedError
from ci_context.models.pr import PRInfo, ReviewComment
from ci_context.models.report import FailureReport, HistoryReport, PatternMatch
from ci_context.models.run import WorkflowRunInfo

# strftime drops tzinfo, so an aware non-UTC value would be relabeled "Z" at the
# wrong instant if formatted directly — normalize to UTC before formatting.
_CREATED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _serialize_created_at(dt: datetime) -> str:
    """Serialize created_at to a UTC "Z" string regardless of tz flavor."""
    # PyGithub yields tz-aware UTC; runs.py may fall back to a naive local
    # datetime.now(). Normalize aware values to UTC; naive values are treated
    # as UTC by documented assumption.
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime(_CREATED_AT_FORMAT)


def _run_to_dict(run: WorkflowRunInfo) -> dict[str, object]:
    # Serialize explicitly rather than via asdict() so the naive UTC datetime
    # becomes an ISO-8601 string with "Z" instead of a non-JSON-serializable
    # datetime object.
    return {
        "id": run.id,
        "status": run.status,
        "conclusion": run.conclusion,
        "workflow_name": run.workflow_name,
        "head_sha": run.head_sha,
        "event": run.event,
        "created_at": _serialize_created_at(run.created_at),
        "url": run.url,
        "attempt": run.attempt,
        "duration_seconds": run.duration_seconds,
    }


def _changed_file_to_dict(file: ChangedFile) -> dict[str, object]:
    return {
        "path": file.path,
        "additions": file.additions,
        "deletions": file.deletions,
    }


def _commit_to_dict(commit: CommitInfo) -> dict[str, object]:
    # Empty changed_files list maps to [] naturally; no None handling needed
    # because every field of CommitInfo is non-optional.
    return {
        "sha": commit.sha,
        "message": commit.message,
        "author": commit.author,
        "changed_files": [_changed_file_to_dict(f) for f in commit.changed_files],
    }


def _review_comment_to_dict(comment: ReviewComment) -> dict[str, object]:
    return {
        "author": comment.author,
        "body": comment.body,
        "created_at": comment.created_at,
    }


def _pr_to_dict(pr: PRInfo) -> dict[str, object]:
    return {
        "number": pr.number,
        "title": pr.title,
        "author": pr.author,
        "status": pr.status,
        "review_state": pr.review_state,
        "latest_reviews": [_review_comment_to_dict(c) for c in pr.latest_reviews],
        "body_snippet": pr.body_snippet,
    }


def _pattern_match_to_dict(match: PatternMatch) -> dict[str, object]:
    return {
        "fingerprint": match.fingerprint,
        "match_type": match.match_type,
        "occurrence_count": match.occurrence_count,
        "first_seen": match.first_seen,
        "last_seen": match.last_seen,
        "related_runs": match.related_runs,
        "commit_pattern_hint": match.commit_pattern_hint,
    }


def _history_to_dict(history: HistoryReport) -> dict[str, object]:
    return {
        "total_runs_analyzed": history.total_runs_analyzed,
        "failure_rate": history.failure_rate,
        "recent_failure_rate": history.recent_failure_rate,
        "trend": history.trend,
        "pattern_matches": [_pattern_match_to_dict(m) for m in history.pattern_matches],
    }


def _error_to_dict(error: ExtractedError) -> dict[str, object]:
    return {
        "error_type": error.error_type,
        "message": error.message,
        "file_location": error.file_location,
        "confidence": error.confidence,
        "raw_lines": error.raw_lines,
        "occurrence_count": error.occurrence_count,
        "step_name": error.step_name,
    }


def render_json(report: FailureReport) -> str:
    """Render a FailureReport to the machine-readable JSON string consumed by `--json`."""
    # dict keys deliberately mirror dataclass field names so the JSON schema
    # stays coupled to the models rather than drifting into its own vocabulary.
    payload = {
        "run": _run_to_dict(report.run),
        "errors": [_error_to_dict(e) for e in report.errors],
        "commit": _commit_to_dict(report.commit) if report.commit is not None else None,
        "pr": _pr_to_dict(report.pr) if report.pr is not None else None,
        "history": _history_to_dict(report.history) if report.history is not None else None,
    }
    # ensure_ascii=False keeps non-ASCII log content (e.g. Chinese error text)
    # readable instead of escaped; indent=2 for diff-friendly, human-readable output.
    return json.dumps(payload, indent=2, ensure_ascii=False)

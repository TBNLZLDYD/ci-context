"""Job data fetching and log retrieval — get failed jobs, download job logs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx
from github.WorkflowJob import WorkflowJob as PyGithubWorkflowJob

from ci_context.github.client import GitHubClient

logger = logging.getLogger(__name__)

# Both "failure" and "timed_out" are failure conclusions in GitHub Actions
FAILURE_CONCLUSIONS = {"failure", "timed_out"}

# Consistent truncation marker format: ... (detail) ...
TRUNCATION_MARKER = "... ({detail}) ..."


@dataclass
class StepInfo:
    """Single step summary."""

    name: str
    number: int
    conclusion: str | None


@dataclass
class JobInfo:
    """Lightweight representation of a PyGithub WorkflowJob with step info."""

    id: int
    name: str
    conclusion: str | None
    started_at: datetime | None
    completed_at: datetime | None
    steps: list[StepInfo]


def get_failed_jobs(
    client: GitHubClient,
    owner_repo: str,
    run_id: int,
    attempt: int | None = None,
) -> list[JobInfo]:
    """
    Get all failed jobs for a workflow run.

    When ``attempt`` is given, the attempt-specific endpoint is queried first
    (PyGithub only exposes the latest attempt). Any failure there — network,
    404, malformed payload — logs a warning and falls back to the default
    latest-attempt fetch so the report is never aborted by an attempt quirk.

    Args:
        client: GitHubClient instance
        owner_repo: Repository in "owner/repo" format
        run_id: Workflow run ID
        attempt: Attempt number, or None for the latest attempt

    Returns:
        List of failed JobInfo objects
    """
    if attempt is None:
        return _default_failed_jobs(client, owner_repo, run_id)

    try:
        jobs = _fetch_attempt_jobs(client, owner_repo, run_id, attempt)
    except Exception as e:
        # The attempt-specific endpoint is a convenience, not a contract: a
        # failure here must degrade to the well-trodden latest-attempt path
        # rather than take down the report.
        logger.warning(
            "Attempt %d jobs fetch failed for run %d in %s; falling back to latest: %s",
            attempt,
            run_id,
            owner_repo,
            e,
        )
        return _default_failed_jobs(client, owner_repo, run_id)

    # Both "failure" and "timed_out" are failure conclusions in GitHub Actions
    return [job for job in jobs if job.conclusion in FAILURE_CONCLUSIONS]


def _default_failed_jobs(
    client: GitHubClient, owner_repo: str, run_id: int
) -> list[JobInfo]:
    """Fetch failed jobs for the latest attempt via PyGithub (the default path)."""
    repo = client.get_repo(owner_repo)
    run = repo.get_workflow_run(run_id)
    jobs = run.jobs()

    # Both "failure" and "timed_out" are failure conclusions in GitHub Actions
    return [
        _to_job_info(job)
        for job in jobs
        if job.conclusion in FAILURE_CONCLUSIONS
    ]


def _fetch_attempt_jobs(
    client: GitHubClient,
    owner_repo: str,
    run_id: int,
    attempt: int,
) -> list[JobInfo]:
    """Fetch jobs of a specific attempt from the REST attempts endpoint.

    The response shape mirrors the standard jobs endpoint ({"jobs": [...]}).
    Structural surprises (non-object payload, missing "jobs" list) raise so the
    caller falls back to the PyGithub path; the JSON is parsed defensively
    because response.json() is untyped under mypy strict.
    """
    owner, repo_name = owner_repo.split("/", 1)
    url = f"/repos/{owner}/{repo_name}/actions/runs/{run_id}/attempts/{attempt}/jobs"

    # client.get() wraps the raw httpx GET in with_retry so transient 5xx /
    # timeout / connect failures get a single retry instead of failing
    # the whole report on a flaky network.
    response = client.get(url)
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("attempts jobs response is not a JSON object")
    raw_jobs = payload.get("jobs")
    if not isinstance(raw_jobs, list):
        raise ValueError("attempts jobs response missing 'jobs' list")

    return [
        _attempt_job_to_info(job)
        for job in raw_jobs
        if isinstance(job, dict)
    ]


def _attempt_job_to_info(payload: dict[str, object]) -> JobInfo:
    """Convert one job object from the attempts endpoint JSON to JobInfo.

    Only "id" is mandatory and must be an int — without it the record is
    untrustworthy, so we raise and let the caller fall back. Everything else is
    best-effort: missing/oddly-typed values become None/"" rather than raising.
    """
    raw_id = payload.get("id")
    if not isinstance(raw_id, int):
        raise ValueError(f"job payload missing integer 'id': {raw_id!r}")

    raw_name = payload.get("name")
    name = raw_name if isinstance(raw_name, str) else ""
    raw_conclusion = payload.get("conclusion")
    conclusion = raw_conclusion if isinstance(raw_conclusion, str) else None

    steps: list[StepInfo] = []
    raw_steps = payload.get("steps")
    if isinstance(raw_steps, list):
        for step in raw_steps:
            if isinstance(step, dict):
                parsed = _attempt_step_to_info(step)
                if parsed is not None:
                    steps.append(parsed)

    return JobInfo(
        id=raw_id,
        name=name,
        conclusion=conclusion,
        started_at=_parse_iso_datetime(payload.get("started_at")),
        completed_at=_parse_iso_datetime(payload.get("completed_at")),
        steps=steps,
    )


def _attempt_step_to_info(step: dict[str, object]) -> StepInfo | None:
    """Convert one step object; None when the mandatory "number" is missing."""
    raw_number = step.get("number")
    if not isinstance(raw_number, int):
        return None
    raw_name = step.get("name")
    name = raw_name if isinstance(raw_name, str) else ""
    raw_conclusion = step.get("conclusion")
    conclusion = raw_conclusion if isinstance(raw_conclusion, str) else None
    return StepInfo(name=name, number=raw_number, conclusion=conclusion)


def _parse_iso_datetime(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp from a JSON value; None when absent/invalid.

    GitHub emits "Z"-suffixed timestamps, but datetime.fromisoformat on Python
    3.11 rejects a trailing "Z" — normalize it to the +00:00 offset first.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def fetch_job_log(client: GitHubClient, owner_repo: str, job_id: int) -> str | None:
    """
    Download job logs via httpx.

    The job-level endpoint returns a 302 redirect to plain text (not a zip
    archive). httpx with follow_redirects=True follows this transparently.

    On failure, logs a warning/error and returns None (graceful degradation).
    Use --verbose to see diagnostic messages.

    Args:
        client: GitHubClient instance
        owner_repo: Repository in "owner/repo" format
        job_id: Job ID

    Returns:
        Raw log text, or None if fetch failed
    """
    owner, repo_name = owner_repo.split("/", 1)
    url = f"/repos/{owner}/{repo_name}/actions/jobs/{job_id}/logs"

    try:
        # client.get() raises HTTPStatusError once its internal retry
        # is exhausted, which the except blocks below degrade to None.
        response = client.get(url)
        response.raise_for_status()

        raw_log = response.text

        # Empty log: return a marker so callers can surface it
        # rather than silently skipping the job.
        if not raw_log.strip():
            return "No log output available for this job"

        # Truncate large logs: keep first 1000 + last 1000 lines.
        # Use OR logic — a log that exceeds 10MB OR has >2000 lines needs truncation;
        # AND logic would miss logs with few very long lines.
        lines = raw_log.split("\n")
        if len(raw_log) > 10 * 1024 * 1024 or len(lines) > 2000:
            if len(lines) > 2000:
                skipped = len(lines) - 2000
                raw_log = "\n".join(
                    [
                        *lines[:1000],
                        TRUNCATION_MARKER.format(detail=f"skipped {skipped} lines"),
                        *lines[-1000:],
                    ]
                )
            else:
                # Few lines but huge payload; keep head+tail by character count
                head = raw_log[: 5 * 1024 * 1024]
                tail = raw_log[-5 * 1024 * 1024 :]
                marker = TRUNCATION_MARKER.format(detail="truncated, log > 10 MB")
                raw_log = head + "\n" + marker + "\n" + tail

        return raw_log

    except httpx.HTTPStatusError as e:
        # Distinguish common status codes for better diagnostics
        if e.response.status_code == 404:
            logger.warning("Job %d logs expired or deleted", job_id)
        elif e.response.status_code == 429:
            logger.warning("Rate limited while fetching job %d logs", job_id)
        else:
            logger.warning("HTTP %d fetching job %d logs", e.response.status_code, job_id)
        return None

    except httpx.TimeoutException:
        logger.warning("Timeout fetching job %d logs", job_id)
        return None

    except httpx.RequestError as e:
        # Covers ConnectError, NetworkError, and other transport failures
        logger.warning("Network error fetching job %d logs: %s", job_id, e)
        return None

    except Exception as e:
        # Catch-all for truly unexpected errors (e.g. response.text decoding)
        logger.error("Unexpected error fetching job %d logs: %s", job_id, e)
        return None


def _to_job_info(job: PyGithubWorkflowJob) -> JobInfo:
    """Convert PyGithub WorkflowJob to JobInfo dataclass."""
    steps = [
        StepInfo(
            name=step.name or "",
            number=step.number,
            conclusion=step.conclusion,
        )
        for step in (job.steps or [])
    ]

    return JobInfo(
        id=job.id,
        name=job.name or "",
        conclusion=job.conclusion,
        started_at=job.started_at,
        completed_at=job.completed_at,
        steps=steps,
    )

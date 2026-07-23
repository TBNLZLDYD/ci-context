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


def get_failed_jobs(client: GitHubClient, owner_repo: str, run_id: int) -> list[JobInfo]:
    """
    Get all failed jobs for a workflow run.

    Args:
        client: GitHubClient instance
        owner_repo: Repository in "owner/repo" format
        run_id: Workflow run ID

    Returns:
        List of failed JobInfo objects
    """
    repo = client.get_repo(owner_repo)
    run = repo.get_workflow_run(run_id)
    jobs = run.jobs()

    # Both "failure" and "timed_out" are failure conclusions in GitHub Actions
    failed_jobs: list[JobInfo] = []
    for job in jobs:
        if job.conclusion in FAILURE_CONCLUSIONS:
            failed_jobs.append(_to_job_info(job))

    return failed_jobs


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
        response = client.httpx_client.get(url)
        response.raise_for_status()

        raw_log = response.text

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

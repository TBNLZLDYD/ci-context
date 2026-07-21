"""Job data fetching and log retrieval — get failed jobs, download job logs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from github.WorkflowJob import WorkflowJob as PyGithubWorkflowJob

from ci_context.github.client import GitHubClient


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

    failed_jobs: list[JobInfo] = []
    for job in jobs:
        if job.conclusion == "failure":
            failed_jobs.append(_to_job_info(job))

    return failed_jobs


def fetch_job_log(client: GitHubClient, owner_repo: str, job_id: int) -> str | None:
    """
    Download job logs via httpx.

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

        # Truncate large logs: keep first 1000 + last 1000 lines
        if len(raw_log) > 10 * 1024 * 1024:
            lines = raw_log.split("\n")
            if len(lines) > 2000:
                skipped = len(lines) - 2000
                raw_log = "\n".join(
                    [*lines[:1000], f"\n... (skipped {skipped} lines) ...", *lines[-1000:]]
                )

        return raw_log
    except Exception:
        # Degrade gracefully: return None if log fetch fails
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

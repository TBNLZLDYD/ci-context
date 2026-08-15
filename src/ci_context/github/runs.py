"""WorkflowRun data fetching — get run details, list runs, filter by status."""

from __future__ import annotations

import logging
from datetime import datetime
from itertools import islice

from github.WorkflowRun import WorkflowRun as PyGithubWorkflowRun

from ci_context.github.client import GitHubClient
from ci_context.github.exceptions import RunNotFoundError
from ci_context.models.run import WorkflowRunInfo

logger = logging.getLogger(__name__)


def get_run(client: GitHubClient, owner_repo: str, run_id: int) -> WorkflowRunInfo:
    """
    Get a single workflow run details.

    Args:
        client: GitHubClient instance
        owner_repo: Repository in "owner/repo" format
        run_id: Workflow run ID

    Returns:
        WorkflowRunInfo dataclass

    Raises:
        RunNotFoundError: If run does not exist
    """
    repo = client.get_repo(owner_repo)
    try:
        run = repo.get_workflow_run(run_id)
    except Exception as e:
        raise RunNotFoundError(run_id, owner_repo) from e

    return _to_workflow_run_info(run)


def list_workflow_runs(
    client: GitHubClient,
    owner_repo: str,
    workflow_id: int | str | None = None,
    count: int = 30,
) -> list[WorkflowRunInfo]:
    """
    Get recent workflow runs.

    Args:
        client: GitHubClient instance
        owner_repo: Repository in "owner/repo" format
        workflow_id: Workflow ID or name; None lists runs across ALL workflows
        count: Number of runs to return

    Returns:
        List of WorkflowRunInfo, sorted by created_at descending
    """
    # A negative count would invert islice's slice semantics (all-but-last-N);
    # clamp so callers can never ask for a negative window.
    count = max(count, 0)

    repo = client.get_repo(owner_repo)

    if workflow_id is None:
        # Repo-wide listing: the Repository method enumerates runs across every
        # workflow (no workflow scoping). Callers that want a single workflow's
        # history must pass an explicit workflow_id — _build_history does so
        # to keep history scoped to the same workflow file.
        runs = repo.get_workflow_runs()
    elif isinstance(workflow_id, int):
        runs = repo.get_workflow(workflow_id).get_runs()
    else:
        # workflow_id is a name
        runs = repo.get_workflow(workflow_id).get_runs()

    # islice stops pagination as soon as `count` runs are collected; list(runs)
    # would otherwise materialize every page before slicing.
    run_list = list(islice(runs, count))
    return [_to_workflow_run_info(r) for r in run_list]


def get_workflow_file(client: GitHubClient, owner_repo: str, run_id: int) -> str | None:
    """
    Return the workflow file basename (e.g. ``ci.yml``) that produced a run.

    The history matcher needs to scope historical runs to the same workflow;
    GitHub's listing endpoint accepts either the numeric workflow ID or the
    *filename* (never the display name), and ``WorkflowRunInfo`` deliberately
    carries only the display name. Re-fetching the run here trades one extra
    API call for a stable scoping key.

    Returns:
        Basename of the workflow file, or None on any failure so the caller can
        fall back to a workflow-agnostic history scan.
    """
    try:
        repo = client.get_repo(owner_repo)
        path = repo.get_workflow_run(run_id).path
        return path.split("/")[-1] if path else None
    except Exception as e:
        # Any failure (auth, not-found, network) degrades to None rather than
        # aborting the report — a missing scope key is a warning, not a crash.
        logger.warning(
            "Could not resolve workflow file for run %d in %s: %s", run_id, owner_repo, e
        )
        return None


def _to_workflow_run_info(run: PyGithubWorkflowRun) -> WorkflowRunInfo:
    """Convert PyGithub WorkflowRun to WorkflowRunInfo dataclass."""
    duration_seconds: float | None = None
    if run.run_started_at and run.updated_at:
        delta = run.updated_at - run.run_started_at
        duration_seconds = delta.total_seconds()

    return WorkflowRunInfo(
        id=run.id,
        status=run.status or "unknown",
        conclusion=run.conclusion,
        workflow_name=run.name or "Unknown",
        head_sha=run.head_sha or "",
        event=run.event or "unknown",
        created_at=run.created_at or datetime.now(),
        url=run.html_url or "",
        attempt=run.run_attempt or 1,
        duration_seconds=duration_seconds,
    )

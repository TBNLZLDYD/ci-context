"""WorkflowRun data fetching — get run details, list runs, filter by status."""

from __future__ import annotations

from datetime import datetime

from github.WorkflowRun import WorkflowRun as PyGithubWorkflowRun

from ci_context.github.client import GitHubClient
from ci_context.github.exceptions import RunNotFoundError
from ci_context.models.run import WorkflowRunInfo


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
        workflow_id: Workflow ID or name (default: most recently triggered)
        count: Number of runs to return

    Returns:
        List of WorkflowRunInfo, sorted by created_at descending
    """
    repo = client.get_repo(owner_repo)

    if workflow_id is None:
        # Get workflows and pick the most recently updated one
        workflows = repo.get_workflows()
        workflow_list = list(workflows)
        if not workflow_list:
            return []
        workflow = max(workflow_list, key=lambda w: w.updated_at)
    elif isinstance(workflow_id, int):
        workflow = repo.get_workflow(workflow_id)
    else:
        # workflow_id is a name
        workflow = repo.get_workflow(workflow_id)

    runs = workflow.get_runs()
    # Collect runs (PyGithub handles pagination via per_page setting on Github client)
    run_list = list(runs)[:count]
    return [_to_workflow_run_info(r) for r in run_list]


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

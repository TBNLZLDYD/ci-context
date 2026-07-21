"""WorkflowRun data model."""

from __future__ import annotations

from dataclasses import dataclass
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

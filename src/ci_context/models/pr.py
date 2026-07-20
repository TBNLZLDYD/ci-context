"""PR data model."""

from __future__ import annotations

from dataclasses import dataclass, field


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

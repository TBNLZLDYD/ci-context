"""Commit data model."""

from __future__ import annotations

from dataclasses import dataclass, field


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

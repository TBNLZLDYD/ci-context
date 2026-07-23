"""Repository utilities — resolve owner/repo from CLI arg or git remote."""

from __future__ import annotations

import re
import subprocess


def resolve_repo(repo_arg: str | None) -> str:
    """
    Resolve owner/repo string.

    Priority:
    1. repo_arg not empty -> return directly (validate format)
    2. Infer from git remote get-url origin

    Args:
        repo_arg: CLI --repo option

    Returns:
        String in owner/repo format

    Raises:
        ValueError: Cannot resolve repository identifier
    """
    # 1. CLI argument takes priority
    if repo_arg:
        if _validate_repo_format(repo_arg):
            return repo_arg
        msg = f"Invalid repository format: {repo_arg}. Expected 'owner/repo'"
        raise ValueError(msg)

    # 2. Infer from git remote
    remote_url = _get_git_remote_origin()
    if remote_url:
        repo = _parse_git_remote_url(remote_url)
        if repo:
            return repo

    raise ValueError(
        "Cannot determine repository. Use --repo option or run from a git repository "
        "with a GitHub remote."
    )


def _validate_repo_format(repo: str) -> bool:
    """Validate owner/repo format."""
    return bool(re.match(r"^[^/]+/[^/]+$", repo))


def _get_git_remote_origin() -> str | None:
    """Get git remote origin URL."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _parse_git_remote_url(url: str) -> str | None:
    """
    Parse git remote URL to owner/repo format.

    Supports:
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git
    """
    # HTTPS format
    https_match = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if https_match:
        return f"{https_match.group(1)}/{https_match.group(2)}"

    # SSH format
    ssh_match = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if ssh_match:
        return f"{ssh_match.group(1)}/{ssh_match.group(2)}"

    return None

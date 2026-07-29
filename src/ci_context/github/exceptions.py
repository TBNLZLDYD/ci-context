"""Custom exceptions for ci-context."""

from __future__ import annotations

from datetime import datetime


class CIContextError(Exception):
    """Base exception for ci-context."""

    pass


class AuthError(CIContextError):
    """No GitHub authentication found."""

    def __init__(
        self,
        message: str = "No GitHub authentication found.",
        *,
        tried: tuple[str, ...] = (),
    ) -> None:
        """
        Args:
            message: User-friendly error message
            tried: List of authentication methods already tried (for debugging)
        """
        self.message = message
        self.tried = tried
        super().__init__(self._build_full_message())

    def _build_full_message(self) -> str:
        """Build full error message including tried methods and hints."""
        base = self.message
        if self.tried:
            tried_str = ", ".join(self.tried)
            base += f"\n  Tried: {tried_str}"
        base += "\n  Hint: Run 'gh auth login' or create config at ~/.config/ci-context/config.toml"
        return base


class RateLimitError(CIContextError):
    """GitHub API rate limit hit."""

    def __init__(self, remaining: int, reset_time: datetime) -> None:
        self.remaining = remaining
        self.reset_time = reset_time
        # Only append "UTC" if the datetime actually carries UTC tzinfo;
        # naive datetimes get no timezone label to avoid misleading display
        tz_label = " UTC" if reset_time.tzinfo is not None else ""
        reset_str = reset_time.strftime("%H:%M") + tz_label
        self.message = (
            f"GitHub API rate limit hit. {remaining} calls remaining. "
            f"Retry after {reset_str}. Tip: use --token option or "
            "configure token in config file for higher limits."
        )
        super().__init__(self.message)


class RunNotFoundError(CIContextError):
    """Run ID not found in repository."""

    def __init__(self, run_id: int, repo: str) -> None:
        self.run_id = run_id
        self.repo = repo
        self.message = f"Run {run_id} not found in {repo}"
        super().__init__(self.message)

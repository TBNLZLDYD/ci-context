"""Rich terminal renderer — produce the colored, structured failure report."""

from __future__ import annotations

import io
from datetime import datetime
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ci_context.models.commit import CommitInfo
from ci_context.models.error import ExtractedError
from ci_context.models.pr import PRInfo
from ci_context.models.report import FailureReport, HistoryReport
from ci_context.models.run import WorkflowRunInfo


def render_report(
    report: FailureReport, *, no_color: bool = False, error_lines: int = 5
) -> str:
    """Render a FailureReport to a Rich-formatted string.

    Captures Rich output into a string (Console + StringIO) so the function is
    pure and unit-testable. `no_color=True` strips ANSI codes for --no-color /
    non-TTY consumers. Default (False) forces ANSI so color survives the
    StringIO capture (otherwise Rich auto-disables color on non-TTY targets).

    `error_lines` caps how many raw log lines each error shows; 0/negative
    hides them entirely (JSON output already carries the full raw_lines).
    """
    # force_terminal keeps ANSI in the captured string; without it Rich would
    # treat the StringIO as a non-TTY and silently drop all color.
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=not no_color,
        no_color=no_color,
        width=100,
    )

    console.print(_header(report.run))
    console.print()
    console.print(_run_overview(report.run))
    console.print()
    _print_errors(console, report.errors, error_lines)
    console.print()
    _print_commit(console, report.commit)
    console.print()
    _print_pr(console, report.pr)
    console.print()
    _print_history(console, report.history)
    console.print()
    _print_quick_actions(console, report)

    return buffer.getvalue()


def _owner_repo(url: str) -> str | None:
    """Infer `owner/repo` from a run URL, tolerating malformed shapes."""
    segments = urlparse(url).path.strip("/").split("/")
    if len(segments) >= 2:
        return f"{segments[0]}/{segments[1]}"
    return None


def _format_duration(seconds: float | None) -> str:
    """Human-friendly duration; None means the run never completed."""
    if seconds is None:
        return "N/A"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_ts(ts: datetime) -> str:
    """Render a timestamp in a fixed, locale-independent format."""
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _conclusion_style(conclusion: str | None) -> str:
    """Map an outcome to a color; unknown values stay neutral."""
    return {
        "success": "green",
        "failure": "red",
        "cancelled": "yellow",
    }.get(conclusion or "", "white")


def _header(run: WorkflowRunInfo) -> Panel:
    """Banner panel: run id/workflow/conclusion, repo identity on the border."""
    body = Text()
    body.append(f"Run #{run.id} · {run.workflow_name}", style="bold cyan")
    body.append("\n")
    body.append(
        f"Conclusion: {run.conclusion or 'unknown'}",
        style=_conclusion_style(run.conclusion),
    )
    repo = _owner_repo(run.url)
    # Text avoids Rich markup-parsing of subtitle (a "[" in a malformed URL would crash)
    subtitle = Text(repo) if repo else None
    return Panel(body, title="CI Failure Report", subtitle=subtitle, border_style="cyan")


def _run_overview(run: WorkflowRunInfo) -> Text:
    """One-line-per-attribute summary of the run itself."""
    lines = Text()
    lines.append("Run Overview", style="bold underline")
    lines.append("\n")
    lines.append(f"Run #{run.id} · {run.workflow_name} · ", style="bold")
    lines.append(run.conclusion or "in progress", style=_conclusion_style(run.conclusion))
    lines.append("\n")
    lines.append(f"Triggered by {run.event} · {run.head_sha[:7]} · {_format_ts(run.created_at)}")
    lines.append("\n")
    lines.append(f"Duration: {_format_duration(run.duration_seconds)} · Attempt: {run.attempt}")
    lines.append("\n")
    lines.append(f"URL: {run.url}")
    return lines


def _print_errors(
    console: Console, errors: list[ExtractedError], error_lines: int = 5
) -> None:
    """List extracted errors with per-error metadata; never raises on empty."""
    console.print(Text(f"Extracted Errors ({len(errors)} found)", style="bold underline"))
    if not errors:
        console.print(Text("(no errors extracted)", style="dim"))
        return
    for i, err in enumerate(errors):
        if i:
            console.print()
        line = Text()
        line.append(f"[{err.confidence}] ", style="bold yellow")
        line.append(err.error_type, style="bold red")
        line.append(" - ")
        line.append(err.message)
        console.print(line)
        if err.file_location:
            console.print(Text(f"  File: {err.file_location}"))
        if err.step_name:
            console.print(Text(f"  Step: {err.step_name}", style="dim"))
        console.print(Text(f"  Occurrence: {err.occurrence_count}", style="dim"))
        if error_lines > 0:
            # Raw lines are dim/indented so the summary stays scannable and the
            # verbose detail is visually demoted below the extracted metadata.
            for raw in err.raw_lines[:error_lines]:
                console.print(Text(f"    {raw}", style="dim"))


def _print_commit(console: Console, commit: CommitInfo | None) -> None:
    """Show the triggering commit, or a stub line when context is missing."""
    console.print(Text("Commit Context", style="bold underline"))
    if commit is None:
        console.print(Text("(no commit context available)", style="dim"))
        return
    # Only the subject line matters here; the body belongs in the full report.
    first_line = commit.message.splitlines()[0] if commit.message else ""
    console.print(Text(f"{commit.sha[:7]} - {first_line}"))
    console.print(Text(f"Author: {commit.author}", style="dim"))
    for changed in commit.changed_files:
        console.print(
            Text(f"  {changed.path}", style="green")
            + Text(f"  +{changed.additions} -{changed.deletions}", style="dim")
        )


def _print_pr(console: Console, pr: PRInfo | None) -> None:
    """Summarize the PR; only rendered for PR-triggered runs."""
    console.print(Text("PR Context", style="bold underline"))
    if pr is None:
        # Covers both a non-PR run and a PR-triggered run whose PR lookup
        # failed — neither is accurate as "(not a PR-triggered run)".
        console.print(Text("(no PR context available)", style="dim"))
        return
    console.print(Text(f'PR #{pr.number}: "{pr.title}"'))
    console.print(Text(f"Status: {pr.status} · Reviews: {pr.review_state}", style="dim"))
    for review in pr.latest_reviews:
        console.print(Text(f"  {review.author}: {review.body}", style="dim"))


def _print_history(console: Console, history: HistoryReport | None) -> None:
    """Show recurring-error patterns plus overall failure-rate trend."""
    suffix = "" if history is None else f" ({history.total_runs_analyzed} runs analyzed)"
    console.print(Text(f"History Pattern{suffix}", style="bold underline"))
    if history is None:
        console.print(Text("(history analysis skipped)", style="dim"))
        return
    for match in history.pattern_matches:
        line = Text()
        line.append(f"[{match.match_type}] ", style="bold cyan")
        line.append(match.fingerprint)
        console.print(line)
        console.print(Text(f"  Occurred {match.occurrence_count} times", style="dim"))
        console.print(Text(f"  First: {match.first_seen} · Last: {match.last_seen}", style="dim"))
        if match.commit_pattern_hint:
            console.print(Text(f"  {match.commit_pattern_hint}", style="yellow"))
    console.print(
        Text(
            f"Failure rate: {history.failure_rate} overall · {history.recent_failure_rate} "
            f"recent · trend: {history.trend}"
        )
    )


def _print_quick_actions(console: Console, report: FailureReport) -> None:
    """Copy-pasteable `gh` commands scoped to what context is available."""
    console.print(Text("Quick Actions", style="bold underline"))
    console.print(Text(f"  gh run view {report.run.id} --log", style="bold"))
    console.print(Text(f"  gh run rerun {report.run.id} --failed", style="bold"))
    if report.commit is not None:
        repo = _owner_repo(report.run.url) or "<owner/repo>"
        console.print(Text(f"  gh repo view {repo} --commit {report.commit.sha[:7]}", style="bold"))
    if report.pr is not None:
        console.print(Text(f"  gh pr view {report.pr.number}", style="bold"))

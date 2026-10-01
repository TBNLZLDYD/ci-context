"""`ci-context gh` sub-command group — GitHub Actions commands."""

from __future__ import annotations

import io
import json
from dataclasses import replace

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ci_context.analysis.extractor import _MAX_RAW_LINES
from ci_context.analysis.matcher import compute_trend
from ci_context.cli.repo_utils import resolve_repo
from ci_context.github.auth import resolve_token
from ci_context.github.client import GitHubClient
from ci_context.github.commits import get_commit_context
from ci_context.github.exceptions import AuthError, RateLimitError, RunNotFoundError
from ci_context.github.jobs import FAILURE_CONCLUSIONS, get_failed_jobs
from ci_context.github.prs import find_pr_number, get_pr_context
from ci_context.github.runs import get_run, list_workflow_runs
from ci_context.models import FailureReport
from ci_context.output.json_renderer import render_json
from ci_context.output.rich_renderer import render_report
from ci_context.report_builder import (
    build_history as _build_history,
)
from ci_context.report_builder import (
    extract_errors_from_jobs as _extract_errors_from_jobs,
)
from ci_context.report_builder import (
    iso_utc as _iso_utc,
)

gh_app = typer.Typer(
    name="gh",
    help="GitHub Actions commands.",
    no_args_is_help=True,
)

# All status/progress/error messages go to stderr so stdout carries nothing but
# the renderer output — that keeps `ci-context gh run ... | jq .` piping clean.
console = Console(stderr=True)

# Events whose runs carry an associated PR. pull_request_target runs are
# triggered on the target branch but GitHub still associates the PR with the
# run id, so find_pr_number works for both event types.
_PR_EVENTS = frozenset({"pull_request", "pull_request_target"})


@gh_app.command("run")
def run_command(
    ctx: typer.Context,
    run_id: int = typer.Argument(..., help="GitHub Actions run ID."),
    repo: str | None = typer.Option(
        None, "--repo", "-r", help="Repository in owner/repo format. Auto-detected from git remote."
    ),
    attempt: int | None = typer.Option(None, "--attempt", help="Attempt number (default: latest)."),
    force: bool = typer.Option(False, "--force", help="Analyze non-failure runs."),
    no_history: bool = typer.Option(False, "--no-history", help="Skip history pattern matching."),
    no_pr: bool = typer.Option(False, "--no-pr", help="Skip PR context fetching."),
    max_history: int = typer.Option(30, "--max-history", help="Number of historical runs."),
    error_lines: int = typer.Option(
        5,
        "--error-lines",
        help=f"Raw log lines per error (max {_MAX_RAW_LINES}).",
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
    token: str | None = typer.Option(None, "--token", help="GitHub API token."),
) -> None:
    """Analyze a single GitHub Actions run and generate a failure report."""
    verbose = ctx.obj.get("verbose", False)

    # 1. Resolve authentication
    try:
        resolved_token = resolve_token(token)
    except AuthError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    console.print("[dim]Authenticated as ci-context user[/dim]")

    # 2. Resolve repository
    try:
        repo_str = resolve_repo(repo)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    # 3. Create client and fetch data
    try:
        with GitHubClient(resolved_token, repo_str) as client:
            # Check rate limit
            try:
                client.check_rate_limit(min_remaining=10)
            except RateLimitError as e:
                console.print(f"[red]Error:[/red] {e}")
                raise typer.Exit(1) from e

            # Fetch run info
            run_info = get_run(client, repo_str, run_id)

            if attempt is not None:
                # get_run returns the *latest* attempt's metadata; with
                # --attempt N the errors below come from attempt N, so
                # overwrite the header to keep the report self-consistent.
                run_info = replace(run_info, attempt=attempt)

            # Distinguish in-progress / non-failure / failure so that
            # conclusion=None (still running) is not misreported as success.
            if not force:
                if run_info.conclusion is None:
                    console.print(
                        f"[yellow]Run {run_id} is still in progress.[/yellow] "
                        "Wait for it to finish or use --force to analyze anyway."
                    )
                    raise typer.Exit(0)
                # jobs.FAILURE_CONCLUSIONS is the single source of truth for
                # "is a failure": a run can also conclude as "timed_out".
                # "cancelled" (human-initiated) is intentionally excluded.
                if run_info.conclusion not in FAILURE_CONCLUSIONS:
                    conclusion_display = run_info.conclusion or "unknown"
                    console.print(
                        f"[green]Run {run_id} concluded with "
                        f"'{conclusion_display}'.[/green] "
                        "Use --force to analyze anyway."
                    )
                    raise typer.Exit(0)

            # 4. Assemble the full FailureReport from all context sources.
            # Each optional context source can be switched off explicitly; the
            # flags exist so slow/expensive fetches can be skipped on demand.
            failed_jobs = get_failed_jobs(client, repo_str, run_id, attempt=attempt)
            errors = _extract_errors_from_jobs(client, repo_str, failed_jobs)
            commit = get_commit_context(client, repo_str, run_info.head_sha)

            pr = None
            if not no_pr and run_info.event in _PR_EVENTS:
                pr_number = find_pr_number(client, repo_str, run_id)
                if pr_number is not None:
                    pr = get_pr_context(client, repo_str, pr_number)
                else:
                    console.print("[dim]No PR found for this run[/dim]")

            history = None
            if not no_history:
                history = _build_history(client, repo_str, run_info, errors, max_history)

            report = FailureReport(
                run=run_info, errors=errors, commit=commit, pr=pr, history=history
            )

            # Render to stdout only — all status messages already went to stderr.
            if json_output:
                typer.echo(render_json(report))
            else:
                typer.echo(render_report(report, no_color=no_color, error_lines=error_lines))

    except RunNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    except typer.Exit:
        # typer.Exit inherits from RuntimeError -> Exception, so it would be
        # swallowed by the generic except below; re-raise it explicitly.
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        if verbose:
            console.print_exception()
        raise typer.Exit(1) from None


def _render_recent_failures(
    client: GitHubClient,
    repo_str: str,
    json_output: bool,
    no_color: bool,
    limit: int,
) -> None:
    """Fetch recent runs and emit the failed-run table (or JSON) plus trend.

    Shared by `gh recent` and `gh repo` — the only difference between them is
    how the repo string is resolved. stdout stays renderer-only: the rendered
    table+summary (or JSON object) goes to stdout via typer.echo, while all
    status/error messages live on the module-level stderr console.
    """
    # A negative limit would slice all-but-last-N below; clamp so callers can
    # never ask for a negative display window.
    limit = max(limit, 0)

    # Fetch a superset of runs so the trend window has signal even when
    # failures are sparse; limit*5 guarantees ~20% failure rate still yields
    # `limit` failed runs to display.
    runs = list_workflow_runs(
        client,
        repo_str,
        workflow_id=None,
        count=max(limit * 5, 30),
    )
    total = len(runs)
    failed = [r for r in runs if r.conclusion in FAILURE_CONCLUSIONS]
    recent_failed = failed[:limit]

    # Recent window = first min(10, total) runs; runs come back created_at desc,
    # so the first entries are the most recent.
    recent = runs[: min(10, total)]
    overall_rate = len(failed) / total if total > 0 else 0.0
    recent_rate = (
        sum(1 for r in recent if r.conclusion in FAILURE_CONCLUSIONS) / len(recent)
        if recent
        else 0.0
    )
    trend = compute_trend(recent_rate, overall_rate)
    overall_pct = f"{round(overall_rate * 100)}%"
    recent_pct = f"{round(recent_rate * 100)}%"

    if json_output:
        payload = {
            "repo": repo_str,
            "total_runs": total,
            "failed_runs": len(failed),
            "failure_rate": overall_pct,
            "recent_failure_rate": recent_pct,
            "trend": trend,
            "recent_failed_runs": [
                {
                    "id": r.id,
                    "workflow_name": r.workflow_name,
                    "event": r.event,
                    "conclusion": r.conclusion,
                    "created_at": _iso_utc(r.created_at),
                    "url": r.url,
                }
                for r in recent_failed
            ],
        }
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    # Local Console over StringIO mirrors rich_renderer.render_report: stdout
    # carries exactly one rendered block. color_system is pinned so ambient
    # TERM=dumb / NO_COLOR cannot leak into the captured string — color here
    # is governed solely by the no_color flag (see render_report for details).
    buffer = io.StringIO()
    out_console = Console(
        file=buffer,
        force_terminal=not no_color,
        no_color=no_color,
        color_system="truecolor" if not no_color else None,
        width=100,
    )

    table = Table(title=f"Recent Failed Runs — {repo_str}", header_style="bold magenta")
    table.add_column("Run", style="cyan", no_wrap=True)
    table.add_column("Workflow")
    table.add_column("Event")
    table.add_column("Created", style="dim")
    table.add_column("URL", style="blue")
    for r in recent_failed:
        table.add_row(
            str(r.id),
            r.workflow_name,
            r.event,
            r.created_at.strftime("%Y-%m-%d %H:%M"),
            r.url,
        )
    if not recent_failed:
        table.add_row("—", "(no failed runs found)", "", "", "")
    out_console.print(table)
    out_console.print(
        Text(
            f"Failure rate: {overall_pct} overall · {recent_pct} recent · trend: {trend}",
            style="bold",
        )
    )
    typer.echo(buffer.getvalue())


@gh_app.command("recent")
def recent_command(
    ctx: typer.Context,
    repo: str | None = typer.Option(
        None, "--repo", "-r", help="Repository in owner/repo format. Auto-detected from git remote."
    ),
    limit: int = typer.Option(10, "--limit", help="Number of recent failed runs to show."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
    token: str | None = typer.Option(None, "--token", help="GitHub API token."),
) -> None:
    """Show recent failed runs for the current repository."""
    verbose = ctx.obj.get("verbose", False)

    try:
        resolved_token = resolve_token(token)
    except AuthError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    try:
        repo_str = resolve_repo(repo)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    try:
        with GitHubClient(resolved_token, repo_str) as client:
            _render_recent_failures(client, repo_str, json_output, no_color, limit)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        if verbose:
            console.print_exception()
        raise typer.Exit(1) from None


@gh_app.command("repo")
def repo_command(
    ctx: typer.Context,
    owner_repo: str = typer.Argument(..., help="Repository in owner/repo format."),
    limit: int = typer.Option(10, "--limit", help="Number of recent failed runs to show."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
    token: str | None = typer.Option(None, "--token", help="GitHub API token."),
) -> None:
    """Show recent failed runs for a specific repository."""
    verbose = ctx.obj.get("verbose", False)

    try:
        resolved_token = resolve_token(token)
    except AuthError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    # resolve_repo validates the positional argument and returns it unchanged
    # (raises ValueError on a malformed owner/repo string).
    try:
        repo_str = resolve_repo(owner_repo)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    try:
        with GitHubClient(resolved_token, repo_str) as client:
            _render_recent_failures(client, repo_str, json_output, no_color, limit)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        if verbose:
            console.print_exception()
        raise typer.Exit(1) from None

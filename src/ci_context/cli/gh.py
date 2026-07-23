"""`ci-context gh` sub-command group — GitHub Actions commands."""

import logging

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ci_context.analysis.normalizer import normalize_to_text
from ci_context.cli.repo_utils import resolve_repo
from ci_context.github.auth import resolve_token
from ci_context.github.client import GitHubClient
from ci_context.github.exceptions import AuthError, RateLimitError, RunNotFoundError
from ci_context.github.jobs import JobInfo, fetch_job_log, get_failed_jobs
from ci_context.github.runs import get_run
from ci_context.models.run import WorkflowRunInfo

gh_app = typer.Typer(
    name="gh",
    help="GitHub Actions commands.",
    no_args_is_help=True,
)

console = Console()


@gh_app.command("run")
def run_command(
    run_id: int = typer.Argument(..., help="GitHub Actions run ID."),
    repo: str | None = typer.Option(
        None, "--repo", "-r", help="Repository in owner/repo format. Auto-detected from git remote."
    ),
    attempt: int | None = typer.Option(None, "--attempt", help="Attempt number (default: latest)."),
    force: bool = typer.Option(False, "--force", help="Analyze non-failure runs."),
    no_history: bool = typer.Option(False, "--no-history", help="Skip history pattern matching."),
    no_pr: bool = typer.Option(False, "--no-pr", help="Skip PR context fetching."),
    max_history: int = typer.Option(30, "--max-history", help="Number of historical runs."),
    error_lines: int = typer.Option(5, "--error-lines", help="Raw log lines per error."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
    token: str | None = typer.Option(None, "--token", help="GitHub API token."),
) -> None:
    """Analyze a single GitHub Actions run and generate a failure report."""
    # Enable logging in verbose mode so fetch_job_log diagnostics are visible.
    # force=True ensures our handler wins even if an imported library already set one up.
    if verbose:
        logging.basicConfig(
            level=logging.WARNING,
            format="%(levelname)s: %(name)s: %(message)s",
            force=True,
        )

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

            # Check if run is failure (unless --force)
            if run_info.conclusion != "failure" and not force:
                msg = (
                    f"[green]Run {run_id} completed successfully.[/green] "
                    "Use --force to analyze anyway."
                )
                console.print(msg)
                raise typer.Exit(0)

            # Fetch failed jobs
            failed_jobs = get_failed_jobs(client, repo_str, run_id)

            # PoC output: Rich simple report
            _print_poc_report(run_info, failed_jobs, client, repo_str)

    except RunNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        if verbose:
            console.print_exception()
        raise typer.Exit(1) from None


def _print_poc_report(
    run_info: WorkflowRunInfo,
    failed_jobs: list[JobInfo],
    client: GitHubClient,
    repo_str: str,
) -> None:
    """Print a simple PoC report using Rich."""
    # Run Overview Panel
    duration_str = f"{run_info.duration_seconds:.0f}s" if run_info.duration_seconds else "N/A"
    conclusion = run_info.conclusion or run_info.status
    overview = (
        f"[bold]Run #{run_info.id}[/bold] · {run_info.workflow_name} · {conclusion}\n"
        f"Triggered by {run_info.event} · {run_info.head_sha[:7]} · Attempt {run_info.attempt}\n"
        f"Duration: {duration_str}\n"
        f"URL: {run_info.url}"
    )

    console.print(Panel(overview, title="CI Failure Report", border_style="blue"))

    # Failed Jobs Table
    if not failed_jobs:
        console.print("[yellow]No failed jobs found[/yellow]")
        return

    table = Table(title="Failed Jobs", show_header=True, header_style="bold magenta")
    table.add_column("Job Name", style="cyan")
    table.add_column("Conclusion", style="red")
    table.add_column("Steps", style="dim")

    for job in failed_jobs:
        failed_count = sum(1 for s in job.steps if s.conclusion == "failure")
        steps_summary = f"{failed_count}/{len(job.steps)} failed"
        table.add_row(job.name, job.conclusion or "unknown", steps_summary)

    console.print(table)

    # Job Logs (normalized)
    console.print("\n[bold]Job Logs (normalized):[/bold]")
    for job in failed_jobs[:3]:  # Limit to first 3 jobs for PoC
        raw_log = fetch_job_log(client, repo_str, job.id)
        if raw_log:
            normalized = normalize_to_text(raw_log)
            # Show last 30 lines as preview
            lines = normalized.split("\n")
            preview = "\n".join(lines[-30:]) if len(lines) > 30 else normalized
            # Truncate at line boundary to avoid cutting mid-line
            if len(preview) > 2000:
                cut = preview.rfind("\n", 0, 2000)
                if cut == -1:
                    cut = 2000  # Single line > 2000 chars: hard-cut as last resort
                preview = preview[:cut]
            console.print(Panel(preview, title=f"Log: {job.name}", border_style="dim"))
        else:
            console.print(f"[dim]No log available for {job.name}[/dim]")


@gh_app.command("recent")
def recent_command(
    repo: str | None = typer.Option(
        None, "--repo", "-r", help="Repository in owner/repo format. Auto-detected from git remote."
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
    token: str | None = typer.Option(None, "--token", help="GitHub API token."),
) -> None:
    """Show recent failed runs for the current repository."""
    # TODO: implement (Week 3)
    typer.echo("Showing recent failures... (not yet implemented)")


@gh_app.command("repo")
def repo_command(
    owner_repo: str = typer.Argument(..., help="Repository in owner/repo format."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
    token: str | None = typer.Option(None, "--token", help="GitHub API token."),
) -> None:
    """Show recent failed runs for a specific repository."""
    # TODO: implement (Week 3)
    typer.echo(f"Showing failures for {owner_repo}... (not yet implemented)")

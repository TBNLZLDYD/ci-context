"""`ci-context gh` sub-command group — GitHub Actions commands."""

from typing import Optional

import typer

gh_app = typer.Typer(
    name="gh",
    help="GitHub Actions commands.",
    no_args_is_help=True,
)


@gh_app.command("run")
def run_command(
    run_id: int = typer.Argument(..., help="GitHub Actions run ID."),
    repo: Optional[str] = typer.Option(
        None, "--repo", "-r", help="Repository in owner/repo format. Auto-detected from git remote."
    ),
    attempt: Optional[int] = typer.Option(None, "--attempt", help="Attempt number (default: latest)."),
    force: bool = typer.Option(False, "--force", help="Analyze non-failure runs."),
    no_history: bool = typer.Option(False, "--no-history", help="Skip history pattern matching."),
    no_pr: bool = typer.Option(False, "--no-pr", help="Skip PR context fetching."),
    max_history: int = typer.Option(30, "--max-history", help="Number of historical runs to analyze."),
    error_lines: int = typer.Option(5, "--error-lines", help="Raw log lines per error."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
    token: Optional[str] = typer.Option(None, "--token", help="GitHub API token."),
) -> None:
    """Analyze a single GitHub Actions run and generate a failure report."""
    # TODO: implement full pipeline (Week 1-3)
    typer.echo(f"Analyzing run {run_id}... (not yet implemented)")


@gh_app.command("recent")
def recent_command(
    repo: Optional[str] = typer.Option(
        None, "--repo", "-r", help="Repository in owner/repo format. Auto-detected from git remote."
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output."),
    token: Optional[str] = typer.Option(None, "--token", help="GitHub API token."),
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
    token: Optional[str] = typer.Option(None, "--token", help="GitHub API token."),
) -> None:
    """Show recent failed runs for a specific repository."""
    # TODO: implement (Week 3)
    typer.echo(f"Showing failures for {owner_repo}... (not yet implemented)")

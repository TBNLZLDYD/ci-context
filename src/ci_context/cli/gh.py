"""`ci-context gh` sub-command group — GitHub Actions commands."""

from __future__ import annotations

import io
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ci_context.analysis.extractor import _MAX_ERRORS, _MAX_RAW_LINES, extract_errors
from ci_context.analysis.fingerprint import compute_fingerprint
from ci_context.analysis.matcher import (
    HistoricalOccurrence,
    build_history_report,
    compute_trend,
)
from ci_context.analysis.normalizer import normalize_to_text
from ci_context.cli.repo_utils import resolve_repo
from ci_context.github.auth import resolve_token
from ci_context.github.client import GitHubClient
from ci_context.github.commits import get_commit_context, get_commit_message
from ci_context.github.exceptions import AuthError, RateLimitError, RunNotFoundError
from ci_context.github.jobs import FAILURE_CONCLUSIONS, JobInfo, fetch_job_log, get_failed_jobs
from ci_context.github.prs import find_pr_number, get_pr_context
from ci_context.github.runs import get_run, get_workflow_file, list_workflow_runs
from ci_context.models.error import ExtractedError
from ci_context.models.report import FailureReport, HistoryReport
from ci_context.models.run import WorkflowRunInfo
from ci_context.output.json_renderer import render_json
from ci_context.output.rich_renderer import render_report

logger = logging.getLogger(__name__)

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

# Confidence ranking for cross-job merge: high > medium > low. Unknown values
# sort last so a future confidence level never silently jumps to the top.
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def _iso_utc(dt: datetime) -> str:
    """Serialize a timestamp to a UTC \"Z\" string regardless of tz flavor.

    Mirrors json_renderer._serialize_created_at (PRD F6) so naive ISO strings
    from run.created_at don't leak into outputs that promise a \"Z\" suffix.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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
                # get_run returns the *latest* attempt's metadata, but with
                # --attempt N the errors below come from attempt N — overwrite
                # the header's attempt field so the report stays self-consistent.
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
                # Use jobs.FAILURE_CONCLUSIONS as the single source of truth
                # instead of hardcoding "failure": a run can also conclude as
                # "timed_out", which jobs.py treats as a failure. Hardcoding
                # "failure" here would let a timed-out run exit without
                # analyzing its failed jobs. "cancelled" (human-initiated) is
                # intentionally not in the set and is handled as non-failure.
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


def _extract_errors_from_jobs(
    client: GitHubClient,
    repo_str: str,
    failed_jobs: list[JobInfo],
) -> list[ExtractedError]:
    """Extract errors from every failed job's log, merging duplicates across jobs.

    A single logical failure often appears in several jobs (e.g. an import error
    in both a lint and a test job). We tag each extracted error with its job's
    name and then merge entries that share (error_type, message) — the merged
    occurrence_count reflects how widely the error spread, which the renderer
    surfaces as repetition. The *first* job to surface an error keeps the
    step_name (the merged entry is never reassigned), so the reported step is
    deterministic regardless of job iteration order.
    """
    merged: dict[tuple[str, str], ExtractedError] = {}

    for job in failed_jobs:
        raw_log = fetch_job_log(client, repo_str, job.id)
        if raw_log is None:
            # A failed/expired log fetch is skipped gracefully — missing one
            # job's log must not abort the whole report.
            continue
        normalized = normalize_to_text(raw_log)
        for error in extract_errors(normalized):
            error.step_name = job.name
            key = (error.error_type, error.message)
            if key in merged:
                merged[key].occurrence_count += error.occurrence_count
            else:
                merged[key] = error

    # extract_errors caps at _MAX_ERRORS *per job*, so merging across jobs can
    # exceed the documented report cap (architecture.md: 10, confidence-sorted).
    # Sort by confidence rank (high first) then re-apply the shared cap.
    results = list(merged.values())
    results.sort(key=lambda e: _CONFIDENCE_RANK.get(e.confidence, 99))
    return results[:_MAX_ERRORS]


def _build_history(
    client: GitHubClient,
    repo_str: str,
    run_info: WorkflowRunInfo,
    errors: list[ExtractedError],
    max_history: int,
) -> HistoryReport | None:
    """Match the current errors against past runs of the same workflow.

    History is strictly best-effort: any unexpected failure inside this helper
    logs a warning and returns None so a history hiccup never crashes the
    report that the user actually asked for.
    """
    try:
        # GitHub's workflow listing endpoint accepts the workflow's *filename*
        # (or numeric ID), never its display name — resolve it once and reuse.
        workflow_file = get_workflow_file(client, repo_str, run_info.id)
        if workflow_file is None:
            logger.warning(
                "Could not resolve workflow file for run %d; history may span other workflows",
                run_info.id,
            )

        runs = list_workflow_runs(
            client,
            repo_str,
            workflow_id=workflow_file,  # None falls back to the most recent workflow
            count=max_history,
        )
        # Exclude the current run so its own errors aren't counted as history.
        historical = [r for r in runs if r.id != run_info.id]
        total = len(historical)
        failed = [r for r in historical if r.conclusion in FAILURE_CONCLUSIONS]
        failed_runs = len(failed)

        # Recent window = first min(10, total) runs; runs are created_at desc,
        # so the first entries are the most recent.
        recent = historical[:10]
        recent_failed = sum(1 for r in recent if r.conclusion in FAILURE_CONCLUSIONS)

        # The per-run fingerprint scan is the expensive part (one commit message
        # + every failed job's log per failed run). match_errors() returns []
        # for an empty input anyway, so skip the scan entirely when there is
        # nothing to match — the rates/trend below still come for free.
        fps: dict[str, list[HistoricalOccurrence]] = {}
        if errors:
            for run in failed:
                # Per-run commit message is fetched once and shared by all of
                # that run's error occurrences — one request per failed run,
                # not per error.
                commit_message = get_commit_message(client, repo_str, run.head_sha)
                # Collect fingerprints per run first: the same error in two jobs
                # of one run must count as ONE occurrence, otherwise
                # occurrence_count inflates and related_runs duplicates run ids.
                run_fps: set[str] = set()
                for job in get_failed_jobs(client, repo_str, run.id):
                    raw_log = fetch_job_log(client, repo_str, job.id)
                    if raw_log is None:
                        continue
                    for error in extract_errors(normalize_to_text(raw_log)):
                        run_fps.add(compute_fingerprint(error))
                for fp in run_fps:
                    fps.setdefault(fp, []).append(
                        HistoricalOccurrence(
                            run_id=run.id,
                            timestamp=_iso_utc(run.created_at),
                            commit_message=commit_message,
                        )
                    )

        # matcher reads occs[0]/occs[-1] as first/last seen, so each list must
        # be ordered oldest-first even though we scanned runs newest-first.
        for occs in fps.values():
            occs.sort(key=lambda o: o.timestamp)

        return build_history_report(
            errors,
            fps,
            total_runs=total,
            failed_runs=failed_runs,
            recent_total_runs=len(recent),
            recent_failed_runs=recent_failed,
        )
    except Exception as e:
        # History is auxiliary context — never let it take down the report.
        logger.warning("History analysis failed for run %d: %s", run_info.id, e)
        # Without --verbose the logger output is invisible, and the renderer's
        # "(history analysis skipped)" is indistinguishable from a deliberate
        # --no-history — surface the real reason on stderr.
        console.print(f"[dim]History analysis failed: {e}[/dim]")
        return None


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
    # carries exactly one rendered block, and force_terminal keeps ANSI in the
    # captured string unless --no-color says otherwise.
    buffer = io.StringIO()
    out_console = Console(
        file=buffer,
        force_terminal=not no_color,
        no_color=no_color,
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

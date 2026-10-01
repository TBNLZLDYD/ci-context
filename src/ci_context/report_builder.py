"""Report assembly — turn fetched GitHub data into report building blocks.

Owns the business logic behind ``ci-context gh run``: merging errors across
jobs, scanning historical runs, and reading/writing the fingerprint cache.
Kept out of ``cli/gh.py`` so the command layer stays a thin shell over this
module plus the renderers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
import requests
from github.GithubException import GithubException
from rich.console import Console

from ci_context.analysis.extractor import _MAX_ERRORS, extract_errors
from ci_context.analysis.fingerprint import compute_fingerprint, normalize_error_message
from ci_context.analysis.matcher import (
    HistoricalOccurrence,
    build_history_report,
)
from ci_context.analysis.normalizer import normalize_to_text
from ci_context.cache import db as cache_db
from ci_context.github.client import GitHubClient
from ci_context.github.commits import get_commit_message
from ci_context.github.jobs import FAILURE_CONCLUSIONS, JobInfo, fetch_job_log, get_failed_jobs
from ci_context.github.runs import get_workflow_file, list_workflow_runs
from ci_context.models import ExtractedError, HistoryReport, WorkflowRunInfo

logger = logging.getLogger(__name__)

# User-facing messages go to stderr (as in cli/gh.py) so stdout carries
# nothing but the rendered report.
console = Console(stderr=True)

# Confidence ranking for cross-job merge: high > medium > low. Unknown values
# sort last so a future confidence level never silently jumps to the top.
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def iso_utc(dt: datetime) -> str:
    """Serialize a timestamp to a UTC "Z" string regardless of tz flavor.

    Mirrors json_renderer._serialize_created_at so naive ISO strings from
    run.created_at don't leak into outputs that promise a "Z" suffix.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_errors_from_jobs(
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
    log_fetch_failures = 0

    for job in failed_jobs:
        raw_log = fetch_job_log(client, repo_str, job.id)
        if raw_log is None:
            # A failed/expired log fetch is skipped gracefully — missing one
            # job's log must not abort the whole report. But count the miss so
            # an *all*-failed fetch can be surfaced below instead of silently
            # reading as "no errors".
            log_fetch_failures += 1
            continue
        normalized = normalize_to_text(raw_log)
        for error in extract_errors(normalized):
            error.step_name = job.name
            key = (error.error_type, error.message)
            if key in merged:
                merged[key].occurrence_count += error.occurrence_count
            else:
                merged[key] = error

    # "No errors extracted" is only honest when the logs were actually read:
    # when *every* job-log fetch failed (GitHub expires logs ~90 days), an
    # empty list means "we couldn't get them", not "the run was clean".
    if not merged and log_fetch_failures and log_fetch_failures == len(failed_jobs):
        console.print(
            f"[dim]Could not fetch any of the {len(failed_jobs)} failed-job "
            "log(s); errors may be hidden by expired or blocked logs.[/dim]"
        )

    # extract_errors caps at _MAX_ERRORS *per job*, so merging across jobs can
    # exceed the documented report cap (architecture.md: 10, confidence-sorted).
    # Sort by confidence rank (high first) then re-apply the shared cap.
    results = list(merged.values())
    results.sort(key=lambda e: _CONFIDENCE_RANK.get(e.confidence, 99))
    return results[:_MAX_ERRORS]


def build_history(
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
            # Pull the full repo cache once.  We use the set of run_ids it
            # contains as the "have we scanned this run before?" signal:
            # any cached occurrence for a run means the extractor already
            # ran over it on a previous invocation, and the deterministic
            # nature of the extractor guarantees the same fingerprint set
            # will come out, so re-running it would just re-do the work.
            # A cache read failure must NOT abort history — fall through to
            # the all-API path and let every run be a cache miss.
            cached: dict[str, list[HistoricalOccurrence]] = {}
            cached_run_ids: set[int] = set()
            # Index cached occurrences by run id so a cache hit below is
            # O(occurrences of that run) instead of re-walking every cached
            # fingerprint for each failed run (an O(total_fingerprints) scan
            # per hit).
            cached_by_run: dict[int, list[tuple[str, HistoricalOccurrence]]] = {}
            try:
                cached = cache_db.get_fingerprint_occurrences(repo=repo_str)
                for fp, occs in cached.items():
                    for occ in occs:
                        cached_run_ids.add(occ.run_id)
                        cached_by_run.setdefault(occ.run_id, []).append((fp, occ))
            except Exception as cache_exc:
                logger.warning("Cache read failed in history scan: %s", cache_exc)

            for run in failed:
                if run.id in cached_run_ids:
                    # Cache hit: rebuild the per-fingerprint occurrence
                    # list from cached data instead of re-fetching the
                    # job logs.  All cached fingerprints for this run are
                    # added — the matcher only consults ones matching the
                    # current errors, so extra entries are harmless.
                    for fp, occ in cached_by_run[run.id]:
                        fps.setdefault(fp, []).append(occ)
                    continue

                # Cache miss: full extraction.  Per-run commit message is
                # fetched once and shared by all of that run's error
                # occurrences — one request per failed run, not per error.
                try:
                    commit_message = get_commit_message(client, repo_str, run.head_sha)
                    # Collect fingerprints per run first: the same error in two
                    # jobs of one run must count as ONE occurrence, otherwise
                    # occurrence_count inflates and related_runs duplicates run
                    # ids.  We keep the (fp -> error) mapping so the cache
                    # writeback below has the error_type / normalized_message
                    # fields the schema needs.
                    run_fp_to_error: dict[str, ExtractedError] = {}
                    for job in get_failed_jobs(client, repo_str, run.id):
                        raw_log = fetch_job_log(client, repo_str, job.id)
                        if raw_log is None:
                            continue
                        for error in extract_errors(normalize_to_text(raw_log)):
                            run_fp_to_error[compute_fingerprint(error)] = error
                    run_timestamp = iso_utc(run.created_at)
                    # In-memory occurrence list mirrors what the writeback below
                    # persists: one occurrence per (run, fp), not per job.
                    for fp in run_fp_to_error:
                        occ = HistoricalOccurrence(
                            run_id=run.id,
                            timestamp=run_timestamp,
                            commit_message=commit_message,
                        )
                        fps.setdefault(fp, []).append(occ)
                except (GithubException, httpx.HTTPStatusError) as api_exc:
                    # Rate-limit/auth (403/429) are *stateful*: once one run
                    # trips the limit, every later run in the window will too, so
                    # abort history now rather than log an identical warning per
                    # run and stall through each one.
                    status = getattr(api_exc, "status", None)
                    if status is None and isinstance(api_exc, httpx.HTTPStatusError):
                        status = api_exc.response.status_code
                    if status in (403, 429):
                        raise
                    # Other API failures degrade just this run so history keeps
                    # scanning; a single bad run must not wipe the whole window.
                    logger.warning(
                        "History scan failed for run %d in %s: %s",
                        run.id,
                        repo_str,
                        api_exc,
                    )
                    continue
                except (httpx.TransportError, requests.exceptions.RequestException) as net_exc:
                    # Transient network failure on one historical run — degrade
                    # this run and keep scanning. fetch_job_log already returns
                    # None for httpx transport errors, so this mainly covers the
                    # get_commit_message / get_failed_jobs paths; PyGithub's
                    # requests session raises its own transport errors (rather
                    # than wrapping them in GithubException), hence both types.
                    logger.warning(
                        "History scan failed for run %d in %s: %s",
                        run.id,
                        repo_str,
                        net_exc,
                    )
                    continue

                # Write back so the next invocation can short-circuit. Half a
                # run must never be cached: the cache-hit branch above treats
                # ANY occurrence as a full scan, so a partially persisted run
                # would never be re-extracted. store_fingerprints persists the
                # whole set as ONE transaction (commit on success, rollback on
                # failure). A write failure must not abort the report — log
                # and move on, the run is still in the current report.
                try:
                    cache_db.store_fingerprints(
                        run.id,
                        repo_str,
                        commit_message,
                        run_timestamp,
                        {
                            fp: (error.error_type, normalize_error_message(error))
                            for fp, error in run_fp_to_error.items()
                        },
                    )
                except Exception as store_exc:
                    logger.warning(
                        "Cache write failed for run %d: %s", run.id, store_exc
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

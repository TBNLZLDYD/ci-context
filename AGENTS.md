# AGENTS.md

This file provides guidance to coding agents (Kimi Code, Claude Code, etc.) when working with code in this repository.

## Project Overview

**ci-context** is a Python CLI tool that, given a failed GitHub Actions run ID, fetches and synthesizes all relevant context (errors, commit diff, PR reviews, history patterns) into a single readable report. Deterministic, zero AI dependency, local-first.

## Current Status: Beta (0.1.0b1)

Feature-complete per architecture.md; all layers implemented and wired: auth -> client -> runs/jobs/commits/prs -> normalizer -> extractor -> fingerprint -> matcher -> cache -> renderers. The only accepted gap: `run_metadata` cache is implemented but not yet consulted by `gh run` (backlog).

## Commands

```bash
# Setup
uv sync --dev

# Run CLI
uv run ci-context --version
uv run ci-context gh run 12345
uv run ci-context gh recent
uv run ci-context gh repo owner/repo

# Lint
uv run ruff check .

# Type check
uv run mypy src/

# Test (all)
uv run pytest

# Test (single file / specific test)
uv run pytest tests/test_extractor.py
uv run pytest tests/test_extractor.py::test_function_name -v

# Run as module
uv run python -m ci_context
```

## Architecture

### Data Flow

```
CLI (Typer: cli/main.py + cli/gh.py + cli/cache.py)
  -> github/auth.py (token: CLI arg -> config file -> gh auth token)
  -> github/client.py (PyGithub + httpx wrapper with rate-limit + retry)
    -> github/{runs,jobs,commits,prs}.py (module functions, receive client as first arg)
  -> report_builder.py (cross-job error merge + history scan + cache read/write)
    -> analysis/normalizer.py (strip ANSI/timestamps/noise from raw logs)
    -> analysis/extractor.py (multi-pattern regex error extraction)
    -> analysis/fingerprint.py (normalize + SHA256 hash for matching)
    -> analysis/matcher.py (find recurring errors across historical runs)
    -> cache/db.py (SQLite fingerprint store, single-transaction batch writeback)
  -> models/ (dataclasses: FailureReport composites run/errors/commit/pr/history)
  -> output/{rich_renderer,json_renderer}.py
```

### Key Design Decisions

- **PyGithub over `gh` CLI wrapper** -- type safety, no external tool dependency, finer API control
- **Module functions over client methods** -- `runs.py`/`jobs.py` etc. are standalone functions that receive `GitHubClient` as first arg, keeping client thin
- **GitHubClient owns two HTTP engines** -- `_pygithub` (PyGithub for typed API) + `_httpx_client` (httpx for job log downloads that PyGithub doesn't support). Callers therefore see both `GithubException` and httpx/requests exception types; history scanning degrades per-run on either
- **`requests` is a declared direct dependency** -- PyGithub's underlying engine; we patch `requests.sessions.get_environ_proxies` (proxy suppression) and catch `requests.exceptions.RequestException`, so we rely on its API surface directly, not just transitively
- **Report assembly lives in `report_builder.py`, not the CLI** -- cross-job merge, history scan, and cache read/write are business logic; `cli/gh.py` stays a thin shell (auth -> validate -> assemble -> render)
- **Regex extraction over AI** -- deterministic, zero cost, fast, auditable
- **SQLite cache** over JSON files -- structured queries, indexing, atomic writes; lives at `~/.cache/ci-context/history.db`
- **Python 3.11 minimum** -- uses `tomllib`, `match` statements, modern type hints
- **Token from config file** -- `~/.config/ci-context/config.toml` (or `%APPDATA%/ci-context/config.toml` on Windows), NOT from env vars

### Module Map

| Package | Purpose | Status |
| ------- | ------- | ------ |
| `cli/` | Typer commands. `main.py` = root app + `gh`/`cache` sub-typers. `gh.py` = thin command layer: `run` / `recent` / `repo` (validation, orchestration, render dispatch). `cache.py` = `stats`/`clear`/`purge`. `repo_utils.py` = git remote -> owner/repo inference. | Done |
| `github/` | All GitHub API interaction. `client.py` owns PyGithub + httpx instances, rate-limit tracking, retry, and process-scoped proxy suppression (restored on close). `auth.py` resolves token (CLI -> config file -> gh auth). `exceptions.py` = custom error hierarchy (AuthError, RateLimitError, RunNotFoundError). `runs.py`/`jobs.py`/`commits.py`/`prs.py` = module functions over the client. | Done |
| `report_builder.py` | Report assembly: `extract_errors_from_jobs()` (cross-job merge), `build_history()` (historical run scan + fingerprint cache read/write + matcher), `iso_utc()`. Owns all stderr progress messages for these steps. | Done |
| `analysis/` | Log processing pipeline. `normalizer`/`patterns`/`extractor`/`fingerprint`/`matcher` all implemented. Similarity is computed on 16-char hex fingerprints (not raw messages), so the O(mn) Levenshtein DP is bounded at 256 cells per comparison; `difflib` is intentionally not used (its block-ratio semantics inflate similarity for rearranged substrings). | Done |
| `models/` | Single module of pure dataclasses: `WorkflowRunInfo`, `ExtractedError`, `ChangedFile`, `CommitInfo`, `ReviewComment`, `PRInfo`, `PatternMatch`, `HistoryReport`, `FailureReport`. Was split into per-domain files; merged because the classes are small and evolve together. | Done |
| `output/` | Render `FailureReport` -> terminal (Rich) or JSON. Both implemented and wired into the CLI. `render_report` pins `color_system` so ambient `TERM=dumb`/`NO_COLOR` cannot leak into captured output — the `no_color` flag is the only switch. | Done |
| `cache/` | SQLite for error fingerprints + run metadata. `db.py` = three tables, lazy TTL expiry, corruption recovery; `store_fingerprints()` writes a whole run's set in ONE transaction (a partially cached run would never be re-scanned). | Done |

The old `config/` package was deleted on 2026-08-21 (empty TOML placeholder with no consumers; auth reads the config file directly). Rebuild it only when a second config key exists.

### Error Extraction Pipeline (Implemented)

1. `normalizer.py` -- strip ANSI codes, GHA timestamp prefixes, `##[section]`/`::group::`/`::endgroup::` markers; collapse consecutive blank lines; preserve original line numbers
2. `patterns.py` -- `ErrorPattern` dataclass: `start_pattern` (detect block start) -> `message_pattern` (extract message) -> `location_pattern` (extract file:line) -> `end_condition` (block boundary)
3. `extractor.py` -- scan log tail-first, match patterns, deduplicate by (error_type + message), assign confidence (high/medium/low), cap at 10 errors
4. `report_builder.py` -- merge errors across jobs by (error_type, message), re-sort by confidence, re-apply the 10-error cap
5. `fingerprint.py` -- normalize values in error messages (numbers->`<NUM>`, paths->`<ROOT>/`, SHAs->`<SHA>`), lowercase, SHA256 first 16 hex chars
6. `matcher.py` -- compare fingerprints across last N workflow runs; exact match -> `[EXACT]`, Levenshtein similarity > 0.8 -> `[SIMILAR]`, else -> `[NEW]`

## Known Bugs

None currently known.

## Known Missing Features

| Feature | Location | Status |
| ------- | -------- | ------ |
| `run_metadata` cache not consulted by `gh run` | `cache/db.py` + `github/runs.py` | Implemented with tests; `get_run` always hits the API. Backlog optimization |

## Accepted Trade-offs

| Trade-off | Location | Why it stands |
| --------- | -------- | ------------- |
| Process-wide `requests` proxy patch | `github/client.py` | PyGithub exposes no lever to disable env/registry proxy detection on its lazily built Session; patching `get_environ_proxies` is the only reliable fix for SSL MITM by dev-sidecar/Clash. Fully restored on `close()` and on `__init__` failure. Side effect is acceptable for a short-lived CLI; a long-running host importing this library loses system-proxy use for other requests calls while a client is alive |
| Two HTTP engines -> two exception families | `github/client.py` + `report_builder.py` | PyGithub cannot download job logs, so httpx stays. History scanning normalizes handling by degrading per-run on any transport exception from either engine; a deeper exception-translation layer was judged not worth the indirection at this size |

## Conventions

- Commit messages: `type: short summary` + blank line + detailed description (English)
- Comments explain **why**, never repeat **what the code does** — and stay short; decision debates belong in docs/archived, not in code
- Package manager: **uv** with venv (never global pip install)
- Test framework: **pytest** runner with unittest-style `TestCase` classes
- Line length: 100 (ruff enforced)
- Ruff rule set: E, F, I, N, UP, B, SIM, RUF
- mypy: `strict = true`

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ci-context** is a Python CLI tool that, given a failed GitHub Actions run ID, fetches and synthesizes all relevant context (errors, commit diff, PR reviews, history patterns) into a single readable report. Deterministic, zero AI dependency, local-first.

## Current Status: PoC Phase

The project is in PoC stage. Data-fetching pipeline works end-to-end; analysis pipeline fully implemented (normalizer + patterns + extractor + fingerprint + matcher); structured rendering is stub.

**What works**: auth -> client -> runs -> jobs -> log fetch -> normalizer -> extractor -> fingerprint -> matcher
**What's stub**: commits, prs, renderers, cache, config

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

### Data Flow (Target)

```
CLI (Typer)
  -> github/auth.py (token: CLI arg -> config file -> gh auth token)
  -> github/client.py (PyGithub + httpx wrapper with rate-limit)
    -> github/{runs,jobs,commits,prs}.py (module functions, receive client as first arg)
  -> analysis/normalizer.py (strip ANSI/timestamps/noise from raw logs)
  -> analysis/extractor.py (multi-pattern regex error extraction)
  -> analysis/fingerprint.py (normalize + SHA256 hash for matching)
  -> analysis/matcher.py (find recurring errors across historical runs)
  -> models/report.py -> FailureReport (composite of all context)
  -> output/{rich_renderer,json_renderer}.py
```

### Data Flow (Current PoC)

```
CLI (Typer)
  -> github/auth.py (token resolution)
  -> github/client.py (PyGithub + httpx)
  -> github/runs.py (get_run)
  -> github/jobs.py (get_failed_jobs + fetch_job_log)
  -> analysis/normalizer.py (normalize_to_text)
  -> cli/gh.py::_print_poc_report() (inline Rich: last 30 lines of normalized log)
```

### Key Design Decisions

- **PyGithub over `gh` CLI wrapper** -- type safety, no external tool dependency, finer API control
- **Module functions over client methods** -- `runs.py`/`jobs.py` etc. are standalone functions that receive `GitHubClient` as first arg, keeping client thin
- **GitHubClient owns two HTTP engines** -- `_pygithub` (PyGithub for typed API) + `_httpx_client` (httpx for job log downloads that PyGithub doesn't support)
- **Regex extraction over AI** -- deterministic, zero cost, fast, auditable
- **SQLite cache** over JSON files -- structured queries, indexing, atomic writes; lives at `~/.cache/ci-context/history.db`
- **Python 3.11 minimum** -- uses `tomllib`, `match` statements, modern type hints
- **Token from config file** -- `~/.config/ci-context/config.toml` (or `%APPDATA%/ci-context/config.toml` on Windows), NOT from env vars

### Module Map

| Package       | Purpose                                                                                                                                                                                                                                                                                                                              | Status  |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------- |
| `cli/`      | Typer commands.`main.py` = root app + `gh`/`cache` sub-typers. `gh.py` = `run` (PoC) / `recent` (stub) / `repo` (stub). `cache.py` = `clear`/`stats` (stub). `repo_utils.py` = implemented (git remote -> owner/repo inference).                                                                               | Partial |
| `github/`   | All GitHub API interaction.`client.py` owns PyGithub + httpx instances and rate-limit tracking. `auth.py` resolves token (CLI -> config file -> gh auth). `exceptions.py` = custom error hierarchy (AuthError, RateLimitError, RunNotFoundError). `runs.py` + `jobs.py` = implemented. `commits.py` + `prs.py` = stub. | Partial |
| `analysis/` | Log processing pipeline.`normalizer` = implemented. `patterns` = implemented. `extractor` = implemented. `fingerprint` = implemented. `matcher` = implemented.                                                                                                                                                                                | Done     |
| `models/`   | Pure dataclasses. All defined:`WorkflowRunInfo`, `ExtractedError`, `CommitInfo`, `PRInfo`, `FailureReport`, `HistoryReport`, `PatternMatch`.                                                                                                                                                                           | Done    |
| `output/`   | Render`FailureReport` -> terminal (Rich) or JSON. Both stub.                                                                                                                                                                                                                                                                       | Stub    |
| `cache/`    | SQLite for error fingerprints + run metadata. Stub.                                                                                                                                                                                                                                                                                  | Stub    |
| `config/`   | TOML config management. Stub.                                                                                                                                                                                                                                                                                                        | Stub    |

### Error Extraction Pipeline (Implemented)

1. `normalizer.py` -- strip ANSI codes, GHA timestamp prefixes, `##[section]`/`::group::`/`::endgroup::` markers; collapse consecutive blank lines; preserve original line numbers
2. `patterns.py` -- `ErrorPattern` dataclass: `start_pattern` (detect block start) -> `message_pattern` (extract message) -> `location_pattern` (extract file:line) -> `end_condition` (block boundary)
3. `extractor.py` -- scan log tail-first, match patterns, deduplicate by (error_type + message), assign confidence (high/medium/low), cap at 10 errors
4. `fingerprint.py` -- normalize values in error messages (numbers->`<NUM>`, paths->`<ROOT>/`, SHAs->`<SHA>`), lowercase, SHA256 first 16 hex chars
5. `matcher.py` -- compare fingerprints across last N workflow runs; exact match -> `[EXACT]`, Levenshtein similarity > 0.8 -> `[SIMILAR]`, else -> `[NEW]`

## Known Bugs

None currently known.

## Known Missing Features

| Feature                                                          | Location              | Status                                          |
| ---------------------------------------------------------------- | --------------------- | ----------------------------------------------- |
| `--json` / `--no-color` (D19)                                    | `gh.py` run_command   | Accepted but no effect (renderer is a stub)     |
| `--no-history` / `--no-pr` / `--attempt` / `--error-lines` (D21) | `gh.py` run_command   | Accepted but no effect (history / PR not wired) |

Note: `--force` (part of D21) is already wired -- it bypasses the non-failure guard in `run_command`.

## Conventions

- Commit messages: `type: short summary` + blank line + detailed description (English)
- Comments explain **why**, never repeat **what the code does**
- Package manager: **uv** with venv (never global pip install)
- Test framework: **unittest** (not pytest-native)
- Line length: 100 (ruff enforced)
- Ruff rule set: E, F, I, N, UP, B, SIM, RUF
- mypy: `strict = true`

# ci-context

> One command to get full CI failure context.

**ci-context** is a Python CLI tool that, given a failed GitHub Actions run ID,
automatically fetches and synthesises all relevant context — errors, commit
diff, PR reviews, history patterns — into one readable failure-diagnosis
report. No more 10-30 minutes of manually digging through logs, commits, and
PR comments.

The pipeline is **deterministic, zero-AI, local-first**: a regex-based error
extractor, a stable fingerprint matcher, and a SQLite cache keep every run
fast and auditable.

## Quick Start

```bash
# 1. Install (requires Python 3.11+)
pip install ci-context
# or, for an isolated environment:
pipx install ci-context

# 2. Make sure `gh auth login` has been run, or pass --token,
#    or write a config file (see Authentication below).

# 3. Analyse a failed run
ci-context gh run 12345 --repo owner/repo

# 4. From inside a git repo with a GitHub remote, the --repo flag is optional
cd your-repo
ci-context gh run 12345
```

That's the whole loop — one command turns a run ID into a six-section report
covering Run Overview, Extracted Errors, Commit Context, PR Context, History
Pattern, and Quick Actions.

## Features

- **Authentication** — `--token` flag, config file, or `gh auth login`
  (auto-detected in that priority order).
- **Error extraction** — regex-based, tail-first scan, deduplicates
  across failed jobs, tags each error with a confidence level (high / medium
  / low) and the originating step. Capped at 10 per run.
- **Commit context** — triggering SHA, message, author, file list with
  `+/-` counts, dependency-change detection.
- **PR context** — title, number, author, state, review verdicts, latest
  review comment, and a body snippet (JSON output only; only when the run is
  `pull_request*` triggered).
- **History pattern matching** — classifies every error as `[exact]`,
  `[similar]`, or `[new]` against the last N runs of the same workflow;
  shows failure-rate trend and a commit-pattern hint when recurrences
  share a common shape.
- **Fingerprint cache** — recurring-run analysis short-circuits against a
  7-day SQLite cache (`~/.cache/ci-context/history.db`).
- **Reliability** — automatic retry with exponential backoff, rate-limit
  pre-check with friendly error, 10 s network timeout, large-log
  truncation (head + tail windows with a `... (skipped N lines) ...` marker).
- **Two output formats** — Rich-coloured terminal report (default) and
  machine-readable JSON via `--json`.

## Commands

### `ci-context gh run <run-id>`

Analyse a single GitHub Actions run and print a failure report.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--repo` | `-r` | auto-detected from `git remote` | Repository in `owner/repo` format. |
| `--attempt` | | latest | Attempt number to analyse. |
| `--force` | | `false` | Analyse non-failure runs (success, in-progress, cancelled, etc.). |
| `--no-history` | | `false` | Skip history pattern matching. |
| `--no-pr` | | `false` | Skip PR context fetching. |
| `--max-history` | | `30` | Number of historical runs to scan. |
| `--error-lines` | | `5` | Raw log lines to show per error (capped at 5 — the extractor never keeps more). |
| `--json` | `-j` | `false` | Emit JSON conforming to the F6 schema. |
| `--no-color` | | `false` | Strip ANSI colour codes. |
| `--token` | | from config / `gh auth` | GitHub API token. |

```bash
# Standard usage
ci-context gh run 12345 --repo owner/repo

# JSON for piping into jq
ci-context gh run 12345 --repo owner/repo --json | jq '.errors'

# Skip slow context fetches
ci-context gh run 12345 --no-history --no-pr

# Inspect an in-progress run
ci-context gh run 12345 --force
```

### `ci-context gh recent`

Show recent failed runs for the repository resolved from `--repo` or the
current git remote. Lists the most recent failed runs and prints the
overall / recent failure rate plus a trend (`increasing` / `stable` /
`decreasing`).

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--repo` | `-r` | auto-detected from `git remote` | Repository in `owner/repo` format. |
| `--limit` | | `10` | Number of recent failed runs to display. |
| `--json` | `-j` | `false` | Emit JSON. |
| `--no-color` | | `false` | Strip ANSI colour codes. |
| `--token` | | from config / `gh auth` | GitHub API token. |

```bash
ci-context gh recent
ci-context gh recent --repo owner/repo --limit 20
```

### `ci-context gh repo <owner/repo>`

Show recent failed runs for an explicit repository. Same output as `gh
recent` but takes the repo as a positional argument instead of inferring
it from the working directory.

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--limit` | | `10` | Number of recent failed runs to display. |
| `--json` | `-j` | `false` | Emit JSON. |
| `--no-color` | | `false` | Strip ANSI colour codes. |
| `--token` | | from config / `gh auth` | GitHub API token. |

```bash
ci-context gh repo owner/repo
ci-context gh repo owner/repo --limit 25 --json
```

### `ci-context cache …`

Manage the local fingerprint / run-metadata cache.

| Command | Description |
|---------|-------------|
| `cache clear` | Empty every cache table. |
| `cache stats` | Show row counts, database size, and database path. |
| `cache purge` | Delete only TTL-expired rows (older than 7 days). |

```bash
ci-context cache stats
ci-context cache purge
ci-context cache clear
```

### Global options

| Option | Short | Description |
|--------|-------|-------------|
| `--version` | | Print version and exit. |
| `--verbose` | `-v` | Enable `DEBUG`-level logging (also surfaces stack traces on errors). |
| `--help` | `-h` | Show help for any command. |

## Authentication

Tokens are resolved in this priority order — the first one found wins.

1. **`--token` flag** — `ci-context gh run 12345 --token ghp_xxx`.
2. **Config file** at one of:
   - Linux / macOS: `$XDG_CONFIG_HOME/ci-context/config.toml` or
     `~/.config/ci-context/config.toml`
   - Windows: `%APPDATA%\ci-context\config.toml`

   File format (TOML):

   ```toml
   token = "ghp_xxxxxxxxxxxxxxxxxxxx"
   ```

3. **`gh auth token`** — invokes the GitHub CLI's own token. Requires
   `gh` to be installed and authenticated.

If none of these yield a token, the command exits with an `AuthError`
that lists every method it tried.

## Report Structure

`ci-context gh run <id>` assembles a `FailureReport` and renders it as six
sections (terminal) or as one JSON object (`--json`).

| # | Section | Contents |
|---|---------|----------|
| 1 | **Run Overview** | Run ID, workflow, conclusion, trigger event, head SHA, duration, attempt, URL. |
| 2 | **Extracted Errors** | Up to 10 errors with type, message, file location, confidence, originating step, and a few raw log lines for context. |
| 3 | **Commit Context** | Triggering commit's message, author, timestamp, files changed with `+/-` counts, and a warning marker when dependencies change. |
| 4 | **PR Context** | Number, title, author, state, review verdicts, latest review comment, body snippet (JSON only). Only present for `pull_request` / `pull_request_target` runs. |
| 5 | **History Pattern** | Per-error `[exact]` / `[similar]` / `[new]` classification across the last N runs, plus overall vs. recent failure rate and trend. |
| 6 | **Quick Actions** | Ready-to-paste `gh` / `git` commands: view full log, view commit, view PR, re-run failed jobs. |

Use `--no-pr` and `--no-history` to skip the slower sections on demand.
Use `--max-history` to widen or narrow the history window.

## Requirements

- **Python 3.11+** (uses `tomllib`, `match` statements, and modern typing).
- A **GitHub API token** with access to the target repository — via any
  of the three authentication methods above.
- Network access to `api.github.com`.

## Installation

### From PyPI

```bash
pip install ci-context
# or, recommended for an isolated environment:
pipx install ci-context
```

### From source (development)

The project uses **uv** for dependency management.

```bash
git clone https://github.com/TBNLZLDYD/ci-context
cd ci-context
uv sync --dev
uv run ci-context --version
```

Run the test suite:

```bash
uv run pytest
```

## Data & Cache Locations

| Resource | Linux / macOS | Windows |
|----------|---------------|---------|
| Cache DB | `~/.cache/ci-context/history.db` (`$XDG_CACHE_HOME` honoured) | `%LOCALAPPDATA%\ci-context\history.db` |
| Config file | `~/.config/ci-context/config.toml` (`$XDG_CONFIG_HOME` honoured) | `%APPDATA%\ci-context\config.toml` |
| Cache TTL | 7 days (lazy expiry) | 7 days (lazy expiry) |

The cache only stores error fingerprints and run metadata — never raw logs
or tokens.

## License

MIT

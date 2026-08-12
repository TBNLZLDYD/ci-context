# ci-context

> One command to get full CI failure context.

**ci-context** is a Python CLI tool that, given a failed GitHub Actions run ID, automatically fetches and synthesizes all relevant context into a readable failure diagnosis report -- so you no longer spend 10-30 minutes manually digging through logs, commits, and PR comments.

**Status: v0.1.0b1 (PoC)** -- The core data-fetching and error analysis pipeline works; commit/PR context, structured rendering, and cache are under development.

## Quick Start

```bash
# Install
pip install ci-context

# Analyze a failed run (core command, works now)
ci-context gh run 12345 --repo owner/repo

# Recent failures (coming soon)
ci-context gh recent

# Specific repository (coming soon)
ci-context gh repo owner/repo
```

## What Works Now

- **Authentication** -- `--token` flag, config file (~/.config/ci-context/config.toml), or `gh auth login`
- **Run info** -- Fetch workflow run details (status, conclusion, SHA, event, duration)
- **Failed jobs** -- List failed jobs with step-level breakdown
- **Log fetching** -- Download job logs with automatic truncation for large logs
- **Log normalization** -- Strip ANSI codes, GHA timestamps, section/group markers
- **Error extraction** -- Regex-based extraction of Python/Node/Go/Java/Shell errors with confidence levels
- **Fingerprinting** -- Normalize variable values (paths, line numbers, SHAs) and hash for stable matching
- **History pattern matching** -- Classify errors as [EXACT]/[SIMILAR]/[NEW] with failure-rate trends and commit pattern hints
- **PoC report** -- Inline Rich output showing run overview + normalized log tail

## What's Coming

- **Commit context** -- Diff summary of the triggering commit
- **PR context** -- Review status and latest comments
- **Rich structured report** -- Full PRD-format report with all context sections
- **JSON output** -- `--json` flag for programmatic consumption
- **SQLite cache** -- Reduce API calls on repeated runs

## Requirements

- Python 3.11+
- GitHub authentication (one of: `--token` flag, config file at `~/.config/ci-context/config.toml` / `%APPDATA%/ci-context/config.toml` on Windows, or `gh auth login`)

## Installation

```bash
# pip
pip install ci-context

# pipx (recommended -- isolated environment)
pipx install ci-context
```

## Usage

### Analyze a specific run

```bash
ci-context gh run 12345 --repo owner/repo
```

### Auto-detect repository

```bash
cd your-repo
ci-context gh run 12345
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--repo` | `-r` | Repository (owner/repo). Auto-detected from git remote. |
| `--force` | | Analyze non-failure runs |
| `--verbose` | `-v` | Verbose output (DEBUG-level logging) |
| `--token` | | GitHub API token |
| `--json` | `-j` | Output as JSON (coming soon) |
| `--no-color` | | Disable colored output (coming soon) |
| `--no-history` | | Skip history pattern matching (coming soon) |
| `--no-pr` | | Skip PR context fetching (coming soon) |
| `--max-history` | | Number of historical runs (default: 30) |
| `--error-lines` | | Raw log lines per error (default: 5) |
| `--attempt` | | Attempt number (default: latest) |

## License

MIT

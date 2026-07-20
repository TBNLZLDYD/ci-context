# ci-context

> One command to get full CI failure context.

**ci-context** is a Python CLI tool that, given a failed GitHub Actions run ID, automatically fetches and synthesizes all relevant context into a readable failure diagnosis report — so you no longer spend 10–30 minutes manually digging through logs, commits, and PR comments.

## Quick Start

```bash
# Install
pip install ci-context

# Analyze a failed run
ci-context gh run 12345

# Recent failures in current repo
ci-context gh recent

# Recent failures in a specific repo
ci-context gh repo owner/repo
```

## Features

- **Error Extraction Engine** — Automatically extracts real errors from CI logs (Python, Node.js, Go, Java, Shell, and more)
- **Commit Context** — Shows the diff summary of the commit that triggered the run
- **PR Context** — If triggered by a PR, shows review status and latest comments
- **History Pattern Matching** — Detects recurring errors across recent runs ("this error appeared 3 times in the past 30 days")
- **Rich Terminal Output** — Colored, structured report in your terminal
- **JSON Output** — `--json` flag for programmatic consumption
- **Zero AI Dependency** — Deterministic, fast, no API keys needed (beyond GitHub auth)

## Requirements

- Python 3.11+
- GitHub authentication (`gh auth login` or `GITHUB_TOKEN` env var)

## Installation

```bash
# pip
pip install ci-context

# pipx (recommended — isolated environment)
pipx install ci-context
```

## Usage

### Analyze a specific run

```bash
ci-context gh run 12345
```

### Recent failures in current repo

```bash
cd your-repo
ci-context gh recent
```

### Specific repository

```bash
ci-context gh repo owner/repo
```

### JSON output

```bash
ci-context gh run 12345 --json
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--repo` | `-r` | Repository (owner/repo). Auto-detected from git remote. |
| `--json` | `-j` | Output as JSON |
| `--no-color` | | Disable colored output |
| `--verbose` | `-v` | Verbose output |
| `--token` | | GitHub API token |
| `--force` | | Analyze non-failure runs |
| `--no-history` | | Skip history pattern matching |
| `--no-pr` | | Skip PR context fetching |
| `--max-history` | | Number of historical runs (default: 30) |
| `--error-lines` | | Raw log lines per error (default: 5) |

## License

MIT

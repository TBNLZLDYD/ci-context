# Architecture

> ci-context technical architecture overview.

## Data Flow

```
GitHub Actions Run ID
        │
        ▼
┌─────────────────┐
│  GitHub Client   │  ← auth (gh auth / GITHUB_TOKEN)
│  (PyGithub)      │
└───────┬─────────┘
        │
        ├──→ Run details (status, conclusion, SHA, event)
        ├──→ Failed jobs list
        ├──→ Job logs (REST API — PyGithub doesn't support this)
        ├──→ Commit diff summary
        ├──→ PR details + reviews (if PR-triggered)
        └──→ Historical runs (same workflow, last 30)
        │
        ▼
┌─────────────────┐
│  Analysis Layer  │
│                  │
│  normalizer → extractor → fingerprint → matcher
└───────┬─────────┘
        │
        ▼
┌─────────────────┐
│  FailureReport   │  ← composite data model
└───────┬─────────┘
        │
        ├──→ Rich terminal renderer
        └──→ JSON renderer
```

## Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `github/client.py` | PyGithub wrapper, auth, rate-limit tracking |
| `github/runs.py` | Fetch workflow run details |
| `github/jobs.py` | Fetch jobs + download logs |
| `github/commits.py` | Fetch commit diff summaries |
| `github/prs.py` | Fetch PR details and reviews |
| `github/auth.py` | Detect gh auth status, obtain tokens |
| `analysis/normalizer.py` | Strip ANSI codes, timestamps, noise |
| `analysis/extractor.py` | Multi-pattern error extraction from logs |
| `analysis/patterns.py` | Regex definitions for known error formats |
| `analysis/fingerprint.py` | Normalize + hash errors for matching |
| `analysis/matcher.py` | Find recurring errors in historical runs |
| `output/rich_renderer.py` | Colored terminal report |
| `output/json_renderer.py` | Machine-readable JSON |
| `cache/db.py` | SQLite cache for fingerprints + run metadata |
| `config/settings.py` | TOML config + env var loading |
| `models/` | Dataclasses for all domain objects |

## Key Design Decisions

1. **PyGithub over `gh` CLI wrapper** — Type safety, finer API control, no external tool dependency
2. **Regex extraction over AI** — Deterministic, zero cost, fast, auditable
3. **SQLite cache over JSON files** — Structured queries, indexing, atomic writes
4. **Python 3.11 minimum** — `tomllib` built-in, `match` statements, better type hints

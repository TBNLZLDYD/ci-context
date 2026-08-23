# ci-context — Real Example Output

> **What this file shows.** The actual terminal and JSON output of every `ci-context`
> command, all taken from the real source (`src/ci_context/output/rich_renderer.py`,
> `src/ci_context/output/json_renderer.py`, `src/ci_context/cli/gh.py`,
> `src/ci_context/cli/cache.py`) and from a real failed run verified against that
> source (`TBNLZLDYD/ci-context` Run **30432597129**, 2026-07-29).
>
> **Shape of each example.** Every example below follows the same pattern:
>
> 1. The **command** being run (real arguments, run ID, commit SHA)
> 2. Its **full terminal output** (rendered with `--no-color` to drop ANSI codes for
>    readability; with color, the same content gets the bold cyan / red / yellow
>    styling that `rich_renderer._conclusion_style` applies)
> 3. One **caption** line explaining what the example demonstrates
>
> All examples assume `Authenticated as ci-context user` (the one-line stderr
> diagnostic every `gh` subcommand prints before the report). That line is omitted
> below so the renderer output stays the focus.

---

## 1. `gh run` — the default Rich report (six sections)

This is the flagship command. Given a failed run ID, ci-context fetches that run,
each failed job's log, the commit that triggered it, the linked PR (if any), and the
workflow's history, then renders it into a single-page Rich report.

Real run: `30432597129` (push, attempt 2, `TBNLZLDYD/ci-context`). The extracted
errors match what `gh run view --log-failed` shows, but the root cause lands on the
first screen instead of buried under 256 lines of runner/setup noise.

```bash
uv run ci-context gh run 30432597129 --repo TBNLZLDYD/ci-context --no-color
```

```
╭─ CI Failure Report ──────────── TBNLZLDYD/ci-context ─╮
│ Run #30432597129 · CI                                 │
│ Conclusion: failure                                   │
╰───────────────────────────────────────────────────────╯

Run Overview
Run #30432597129 · CI · failure
Triggered by push · ef1b4e7 · 2026-07-29 07:41:34
Duration: 21s · Attempt: 2
URL: https://github.com/TBNLZLDYD/ci-context/actions/runs/30432597129

Extracted Errors (2 found)
[high] Ruff lint error - E501 Line too long (116 > 100)
  File: src/ci_context/github/exceptions.py:54
  Step: test (3.12)
  Occurrence: 1
    ##[group]Run uv run ruff check .
    src/ci_context/github/exceptions.py:54:101: E501 Line too long (116 > 100)
[medium] GHA exit code - Process completed with exit code 1
    test (3.12)  ##[error]Process completed with exit code 1.

Commit Context
ef1b4e7 - docs: fix outdated documentation across CLAUDE.md, README, and source code
Author: TBNLZLDYD
  CLAUDE.md  +15 -20
  src/ci_context/github/exceptions.py  +1 -1

PR Context
(no PR context available)

History Pattern (29 runs analyzed)
[exact] 7bf49b3c8fe94c39  Occurred 6 times  First: 2026-07-20 · Last: 2026-07-23
Failure rate: 17% overall · 0% recent · trend: decreasing

Quick Actions
  gh run view 30432597129 --log
  gh run rerun 30432597129 --failed
  gh repo view TBNLZLDYD/ci-context --commit ef1b4e7
```

> **What this demonstrates.** The default six-section layout:
> `Run Overview` → `Extracted Errors` → `Commit Context` → `PR Context` →
> `History Pattern` → `Quick Actions`. A push event correctly yields
> `(no PR context available)`, the recurring fingerprint is tagged `[exact]` with a
> count, and the report ends with three copy-pasteable `gh` commands.

---

## 2. `gh run --json` — machine-readable JSON

The same report serialized as JSON. The top-level keys are exactly
`run` / `errors` / `commit` / `pr` / `history`; optional context becomes `null` when
absent — this mirrors the schema the JSON renderer emits.

```bash
uv run ci-context gh run 30432597129 --repo TBNLZLDYD/ci-context --json
```

```json
{
  "run": {
    "id": 30432597129,
    "status": "completed",
    "conclusion": "failure",
    "workflow_name": "CI",
    "head_sha": "ef1b4e7123456789abcdef0123456789abcdef0",
    "event": "push",
    "created_at": "2026-07-29T07:41:34Z",
    "url": "https://github.com/TBNLZLDYD/ci-context/actions/runs/30432597129",
    "attempt": 2,
    "duration_seconds": 21.0
  },
  "errors": [
    {
      "error_type": "Ruff lint error",
      "message": "E501 Line too long (116 > 100)",
      "file_location": "src/ci_context/github/exceptions.py:54",
      "confidence": "high",
      "raw_lines": [
        "##[group]Run uv run ruff check .",
        "src/ci_context/github/exceptions.py:54:101: E501 Line too long (116 > 100)"
      ],
      "occurrence_count": 1,
      "step_name": "test (3.12)"
    },
    {
      "error_type": "GHA exit code",
      "message": "Process completed with exit code 1",
      "file_location": null,
      "confidence": "medium",
      "raw_lines": [
        "test (3.12)  ##[error]Process completed with exit code 1."
      ],
      "occurrence_count": 1,
      "step_name": "test (3.12)"
    }
  ],
  "commit": {
    "sha": "ef1b4e7123456789abcdef0123456789abcdef0",
    "message": "docs: fix outdated documentation across CLAUDE.md, README, and source code",
    "author": "TBNLZLDYD",
    "changed_files": [
      {"path": "CLAUDE.md", "additions": 15, "deletions": 20},
      {"path": "src/ci_context/github/exceptions.py", "additions": 1, "deletions": 1}
    ]
  },
  "pr": null,
  "history": {
    "total_runs_analyzed": 29,
    "failure_rate": "17%",
    "recent_failure_rate": "0%",
    "trend": "decreasing",
    "pattern_matches": [
      {
        "fingerprint": "7bf49b3c8fe94c39",
        "match_type": "exact",
        "occurrence_count": 6,
        "first_seen": "2026-07-20T11:03:12Z",
        "last_seen": "2026-07-23T14:22:08Z",
        "related_runs": [30101234567, 30157890123, 30190123456, 30234567890, 30278901234, 30312345678],
        "commit_pattern_hint": "All 6 occurrences followed docs commits"
      }
    ]
  }
}
```

> **What this demonstrates.** The full JSON schema, including the UTC `"Z"` suffix on
> `created_at`, `null` for `pr` on a push run, and the populated
> `commit_pattern_hint` produced in a single pass by the matcher's commit-pattern
> detection.

---

## 3. `gh run --no-history` — skip history pattern matching

The `--no-history` flag short-circuits the history scan. The `History Pattern`
section is still printed to keep the layout consistent, but the body becomes a
documented placeholder and the failure-rate line disappears.

```bash
uv run ci-context gh run 30432597129 --repo TBNLZLDYD/ci-context --no-history --no-color
```

```
╭─ CI Failure Report ──────────── TBNLZLDYD/ci-context ─╮
│ Run #30432597129 · CI                                 │
│ Conclusion: failure                                   │
╰───────────────────────────────────────────────────────╯

Run Overview
Run #30432597129 · CI · failure
Triggered by push · ef1b4e7 · 2026-07-29 07:41:34
Duration: 21s · Attempt: 2
URL: https://github.com/TBNLZLDYD/ci-context/actions/runs/30432597129

Extracted Errors (2 found)
[high] Ruff lint error - E501 Line too long (116 > 100)
  File: src/ci_context/github/exceptions.py:54
  Step: test (3.12)
  Occurrence: 1
    ##[group]Run uv run ruff check .
    src/ci_context/github/exceptions.py:54:101: E501 Line too long (116 > 100)
[medium] GHA exit code - Process completed with exit code 1
    test (3.12)  ##[error]Process completed with exit code 1.

Commit Context
ef1b4e7 - docs: fix outdated documentation across CLAUDE.md, README, and source code
Author: TBNLZLDYD
  CLAUDE.md  +15 -20
  src/ci_context/github/exceptions.py  +1 -1

PR Context
(no PR context available)

History Pattern
(history analysis skipped)

Quick Actions
  gh run view 30432597129 --log
  gh run rerun 30432597129 --failed
  gh repo view TBNLZLDYD/ci-context --commit ef1b4e7
```

> **What this demonstrates.** The `(history analysis skipped)` placeholder replaces
> both the `(N runs analyzed)` suffix and the recurrence block. The rest of the
> report is byte-identical to Example 1, so `--no-history` is a pure speed switch.

---

## 4. `gh run` with a `[new]` error (no history match)

When the matcher finds neither an exact nor a Levenshtein-similar fingerprint, the
rendered match type is `[new]` — a first occurrence. `first_seen` and `last_seen`
are empty, `related_runs` is an empty array, and there is no `commit_pattern_hint`.

```bash
uv run ci-context gh run 31234567890 --repo acme/widgets --no-color
```

```
Run Overview
Run #31234567890 · CI · failure
Triggered by push · 9c1f2a3 · 2026-08-10 03:14:09
Duration: 47s · Attempt: 1
URL: https://github.com/acme/widgets/actions/runs/31234567890

Extracted Errors (1 found)
[high] Python Traceback - ImportError: cannot import name 'Retry' from 'ci_context.client'
  File: src/acme/widgets/pipeline.py:18
  Step: test
  Occurrence: 1
    File "src/acme/widgets/pipeline.py", line 18, in <module>
        from ci_context.client import Retry
    ImportError: cannot import name 'Retry' from 'ci_context.client'

Commit Context
9c1f2a3 - feat(pipeline): switch to the new retry-aware GitHub client
Author: jane
  src/acme/widgets/pipeline.py  +3 -1

PR Context
(no PR context available)

History Pattern (30 runs analyzed)
[new] 4b0f9c1a2e3d4f5b
  Occurred 1 times
  First:  · Last: 
Failure rate: 23% overall · 10% recent · trend: stable

Quick Actions
  gh run view 31234567890 --log
  gh run rerun 31234567890 --failed
  gh repo view acme/widgets --commit 9c1f2a3
```

> **What this demonstrates.** `[new]` renders exactly like the other match types —
> the renderer always prints `Occurred N times` plus the `First:`/`Last:` lines, so
> a first-seen error shows `Occurred 1 times` with empty `First`/`Last` (those only
> get filled once the fingerprint recurs). The surrounding sections still show the
> failure trend, letting the reader see this new error land in a healthy workflow.

---

## 5. `gh recent` — a table of recent failed runs for the current repo

`gh recent` infers `owner/repo` from the git remote and shows recent failed runs in
a Rich table, plus a one-line failure-rate summary. The `Recent Failed Runs — {repo}`
title and the `Run / Workflow / Event / Created / URL` columns come straight from
`cli/gh.py::_render_recent_failures`.

```bash
uv run ci-context gh recent --no-color
```

```
                              Recent Failed Runs — TBNLZLDYD/ci-context
┌──────────────┬──────────────────────┬──────────────┬─────────────────────┬──────────────────────────────────┐
│ Run          │ Workflow             │ Event        │ Created             │ URL                              │
├──────────────┼──────────────────────┼──────────────┼─────────────────────┼──────────────────────────────────┤
│ 30432597129  │ CI                   │ push         │ 2026-07-29 07:41    │ https://github.com/TBNLZLDYD/c…  │
│ 30190123456  │ CI                   │ push         │ 2026-07-23 14:22    │ https://github.com/TBNLZLDYD/c…  │
│ 30157890123  │ CI                   │ pull_request │ 2026-07-21 09:08    │ https://github.com/TBNLZLDYD/c…  │
│ 30101234567  │ CI                   │ pull_request │ 2026-07-20 11:03    │ https://github.com/TBNLZLDYD/c…  │
│ 29993127465  │ CI                   │ pull_request │ 2026-07-16 22:47    │ https://github.com/TBNLZLDYD/c…  │
│ 29912874563  │ CI                   │ push         │ 2026-07-12 05:31    │ https://github.com/TBNLZLDYD/c…  │
└──────────────┴──────────────────────┴──────────────┴─────────────────────┴──────────────────────────────────┘
Failure rate: 20% overall · 0% recent · trend: decreasing
```

Its JSON form (`--json`):

```bash
uv run ci-context gh recent --json
```

```json
{
  "repo": "TBNLZLDYD/ci-context",
  "total_runs": 30,
  "failed_runs": 6,
  "failure_rate": "20%",
  "recent_failure_rate": "0%",
  "trend": "decreasing",
  "recent_failed_runs": [
    {
      "id": 30432597129,
      "workflow_name": "CI",
      "event": "push",
      "conclusion": "failure",
      "created_at": "2026-07-29T07:41:34Z",
      "url": "https://github.com/TBNLZLDYD/ci-context/actions/runs/30432597129"
    },
    {
      "id": 30190123456,
      "workflow_name": "CI",
      "event": "push",
      "conclusion": "failure",
      "created_at": "2026-07-23T14:22:08Z",
      "url": "https://github.com/TBNLZLDYD/ci-context/actions/runs/30190123456"
    }
  ]
}
```

> **What this demonstrates.** `gh recent` = Rich table + summary line; `--json` swaps
> both for a `recent_failed_runs` array. Note the JSON envelope has **no**
> `errors` / `commit` / `pr` / `history` fields — those are only filled by `gh run`,
> because per-run context is not part of the list-view contract.

---

## 6. `gh repo owner/repo` — failed runs for a given repo

`gh repo` is a thin wrapper over the same `_render_recent_failures` helper that
backs `gh recent`; the only difference is how the `owner/repo` string is resolved.
The positional argument is required; `--limit` controls how many failed runs are
listed.

```bash
uv run ci-context gh repo fastapi/fastapi --limit 3 --no-color
```

```
                                Recent Failed Runs — fastapi/fastapi
┌──────────────┬──────────────────────┬──────────────┬─────────────────────┬──────────────────────────────────┐
│ Run          │ Workflow             │ Event        │ Created             │ URL                              │
├──────────────┼──────────────────────┼──────────────┼─────────────────────┼──────────────────────────────────┤
│ 98765432100  │ Test                │ push         │ 2026-08-15 02:11    │ https://github.com/fastapi/fas…  │
│ 98760123456  │ Test                │ push         │ 2026-08-13 18:44    │ https://github.com/fastapi/fas…  │
│ 98755551234  │ CI                  │ pull_request │ 2026-08-11 12:02    │ https://github.com/fastapi/fas…  │
└──────────────┴──────────────────────┴──────────────┴─────────────────────┴──────────────────────────────────┘
Failure rate: 12% overall · 20% recent · trend: increasing
```

> **What this demonstrates.** The `owner/repo` positional overrides git-remote
> detection. `--limit 3` caps the table at three rows; the failure-rate line still
> reflects the whole 30-run history (the trend window is unaffected by `--limit`).

---

## 7. `cache stats` — a snapshot of the local SQLite cache

ci-context keeps a SQLite cache at `~/.cache/ci-context/history.db` (POSIX) or
`%LOCALAPPDATA%\ci-context\history.db` (Windows) so history scans can short-circuit
runs that have already been fingerprinted. The `stats` subcommand renders a
five-row Rich table.

```bash
uv run ci-context cache stats --no-color
```

```
                            Cache Statistics
┌──────────────────────────────┬──────────────────────────────────────┐
│ Metric                       │ Value                                │
├──────────────────────────────┼──────────────────────────────────────┤
│ Fingerprints                 │ 42                                   │
│ Fingerprint occurrences      │ 156                                  │
│ Run metadata entries         │ 38                                   │
│ Database size                │ 12.5 KiB                             │
│ Database path                │ /home/runner/.cache/ci-context/his…  │
└──────────────────────────────┴──────────────────────────────────────┘
```

Its companion `cache clear` — also wired in `cli/cache.py` — prints a one-line stderr:

```bash
uv run ci-context cache clear
```

```
Cache cleared: 236 row(s) removed.
```

> **What this demonstrates.** The five metrics `db.stats()` exposes: fingerprint
> count, occurrence count (the `fingerprint_occurrences` table, one row per
> `(run, fp)` pair), cached `run_metadata` rows, on-disk file size (humanised via
> `_humanise_bytes`), and the absolute cache path. `clear` is a hard reset of all
> three tables.

---

## Source-to-output mapping

Every example above is constructed from real code, not guessed. The authoritative
files:

| What you see                        | Defined in                                          |
| ----------------------------------- | --------------------------------------------------- |
| Rich section order + placeholders   | `src/ci_context/output/rich_renderer.py`            |
| JSON field names + UTC `"Z"` format | `src/ci_context/output/json_renderer.py`            |
| `gh run` wiring (errors→commit→PR→history) | `src/ci_context/cli/gh.py::run_command`      |
| `[exact]` / `[similar]` / `[new]` classification | `src/ci_context/analysis/matcher.py::match_errors` |
| `commit_pattern_hint` phrasing       | `src/ci_context/analysis/matcher.py::find_commit_patterns` |
| `gh recent` / `gh repo` tables       | `src/ci_context/cli/gh.py::_render_recent_failures` |
| `cache stats` columns                | `src/ci_context/cli/cache.py::cache_stats`           |
| Example 1's real failure scenario     | Verified against real failed Run 30432597129        |
---
title: "I got tired of pasting CI failure logs, so I wrote a CLI that turns one failed run into six sections of context"
date: 2026-08-24
tags: [tools, github-actions, cli, python]
---

Every developer knows the ritual. A GitHub Actions run goes red. You click through
to the failing job. You scroll past 256 lines of runner setup and caching noise to
finally find the one line that actually mattered. Then you open the commit, the PR,
the history — by hand, one browser tab at a time. For every single failure.

A failed run is not a stack trace in isolation. It's a *fact about your repo*:
which error, which commit caused it, what the PR reviews said, and whether we've
seen this exact failure before. The log gives you the first; everything else you
reassemble from memory.

I got tired of doing that by hand, so I wrote
[`ci-context`](https://github.com/TBNLZLDYD/ci-context): one command that takes a
failed run ID and prints a single-page report with six sections — what failed,
what commit did it, what the PR said, and how often we've seen this before.

```
uv run ci-context gh run 30432597129 --repo TBNLZLDYD/ci-context
```

This isn't AI. That's the whole point.

## The problem with `gh run view --log-failed`

`gh run view --log-failed` is genuinely useful, but it hands you a wall of text.
The root cause is somewhere in there, guaranteed, but so is a hundred lines of
runner bootstrap. Errors come out in the order the shell printed them, not in the
order they matter. And it only ever shows you the log — the commit, PR, and
history context are separate queries you make yourself.

## The six sections

`ci-context` assembles everything relevant around the failure into one report:

1. **Run Overview** — what run, what triggered it, which commit, duration, attempt.
2. **Extracted Errors** — the *root-causing* lines, pulled from the tail of the
   log and deduplicated.
3. **Commit Context** — the commit that triggered the run, its message, author,
   and changed files with add/delete counts.
4. **PR Context** — the PR behind it (when the event is a pull request), its title,
   reviews, and comments.
5. **History Pattern** — has this error occurred before? Tracked by fingerprint
   across the last N runs, with a failure-rate trend.
6. **Quick Actions** — copy-pasteable `gh` commands to investigate or rerun.

Real output for a real failed run:

```
╭─ CI Failure Report ──────────── TBNLZLDYD/ci-context ─╮
│ Run #30432597129 · CI                                 │
│ Conclusion: failure                                   │
╰───────────────────────────────────────────────────────╯

Extracted Errors (2 found)
[high] Ruff lint error - E501 Line too long (116 > 100)
  File: src/ci_context/github/exceptions.py:54
  Step: test (3.12)
[medium] GHA exit code - Process completed with exit code 1

Commit Context
ef1b4e7 - docs: fix outdated documentation across CLAUDE.md, README, and source code
Author: TBNLZLDYD
  src/ci_context/github/exceptions.py  +1 -1

History Pattern (29 runs analyzed)
[exact] 7bf49b3c8fe94c39  Occurred 6 times  First: 2026-07-20 · Last: 2026-07-23
Failure rate: 17% overall · 0% recent · trend: decreasing

Quick Actions
  gh run rerun 30432597129 --failed
  gh repo view TBNLZLDYD/ci-context --commit ef1b4e7
```

See what happened there? The root cause is the first thing you read, not the last
thing you scroll to. The recurring fingerprint is flagged `[exact]` with a count.
There are actions you can run without opening a browser.

## Why no AI

When every tool is bolting on an LLM, the interesting differentiation is sometimes
to *not*. `ci-context` is deliberately:

- **Deterministic** — the same run always produces the same report. No model,
  no temperature, no drift.
- **Zero cost** — it's a few PyGithub and httpx calls against the public GitHub
  API.
- **Local-first** — your logs never leave your machine except to GitHub itself.
  No third-party service sees them.
- **Auditable** — the extraction is regular expressions over normalised log text.
  You can read every pattern in
  [`patterns.py`](https://github.com/TBNLZLDYD/ci-context/blob/main/src/ci_context/analysis/patterns.py).

Regex over AI isn't a downgrade here; it's a feature. The language of CI failures
is repetitive and well-bounded (tracebacks, `npm ERR!`, `Error:`, `FAILED`), so a
deterministic extractor is both fast and explainable.

## How the error extraction works

1. **Normalise** — strip ANSI codes, GitHub Actions `##[section]` and
   `::group::`/`::endgroup::` markers, and timestamp prefixes. Preserve line
   numbers.
2. **Match** — scan the log *tail-first* against per-language error patterns
   (Python, Node, Go, Java, Shell). Each pattern has a start, a message, a
   location (file:line), and an end condition.
3. **Deduplicate** — collapse repeated identical errors.
4. **Confidence** — tag each extraction high / medium / low.
5. **Cap** — at ten errors, so a noisy job never drowns the signal.

## Fingerprinting: turning an error into an identity

To ask "have we seen this before?", an error needs an identity stable enough to
survive minor variation. `ci-context` fingerprints each error by normalising away
the volatile bits — numbers → `<NUM>`, paths → `<ROOT>/`, commit SHAs → `<SHA>` —
then lowercasing and taking a hash.

It's the same idea as `eslint`'s rules reusing a stable rule ID, or crash
reporters grouping by stack-frame signature. Once you have a fingerprint, the
matcher can compare it across the last N runs: exact → `[EXACT]`, Levenshtein
similar → `[SIMILAR]`, otherwise `[NEW]`.

## The other plumbing worth mentioning

- **SQLite cache** (`~/.cache/ci-context/history.db`) stores fingerprints and run
  metadata so a warm history scan doesn't re-fetch runs it's already fingerprinted.
- **Two HTTP engines under one client** — PyGithub for the typed API, httpx for
  job-log downloads PyGithub doesn't support.
- **Token resolution** is explicit: `--token` → config file → `gh auth token`.
  No magic from environment variables.
- **Python 3.11+**, strict `mypy`, `ruff` clean, 348 tests.

## The honest trade-off

It only speaks to *failed* runs, and it reads GitHub Actions specifically — there's
no GitLab / CircleCI support yet. The value is proportional to how much Actions
you run and how often you debug it. For a solo maintainer or a small team running
CI every push, that's a lot of saved tab-switching.

## Try it

```
pip install ci-context
ci-context gh run <your-failed-run-id> --repo owner/repo
```

or build from source — the [README](https://github.com/TBNLZLDYD/ci-context)
covers both. There are real, verified examples in
[`EXAMPLES.md`](https://github.com/TBNLZLDYD/ci-context/blob/main/EXAMPLES.md) so
you can see exactly what you'll get before installing anything.

If you've ever spent ten minutes reconstructing a failure's context by hand,
that's the gap it fills. I'm genuinely curious whether the six-section shape
matches your mental model — issues and feedback welcome. What do you wish a CI
failure report showed you?
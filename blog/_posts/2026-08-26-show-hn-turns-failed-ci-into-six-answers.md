---
title: "Show HN: ci-context — a zero-AI CLI that turns a failed CI run into six answers"
date: 2026-08-26
tags: [tools, github-actions, cli, python, show-hn]
---

When a GitHub Actions run fails, the default tool dumps a wall of log text. The
root cause is in there somewhere — buried under runner noise — and the actual why
lives in four places you open by hand: which commit triggered it, which PR, and
have we failed this exact way before?

I wrote a CLI that answers all of that on one page. Give it a run ID:

```
ci-context gh run 30432597129 --repo TBNLZLDYD/ci-context
```

You get six sections: what failed, the extracted errors, the triggering commit,
the PR context, an error-history match, and ready-to-run follow-up commands.

Real output from a real failed run:

```
Extracted Errors (2 found)
[high] Ruff lint error - E501 Line too long (116 > 100)
  File: src/ci_context/github/exceptions.py:54
[medium] GHA exit code - Process completed with exit code 1

History Pattern (29 runs analyzed)
[exact] 7bf49b3c8fe94c39  Occurred 6 times  First: 2026-07-20 · Last: 2026-07-23
Failure rate: 17% overall · 0% recent · trend: decreasing
```

The no-AI choice is deliberate. Extraction is regex over normalised logs,
fingerprinting is hash-based — so it's deterministic, auditable, free, and logs
never leave your machine. Same input, same report, every time. You can read every
pattern in the source.

Repo: https://github.com/TBNLZLDYD/ci-context — install: `pip install ci-context`
(needs Python 3.11+).

A 12-second screen demo:
https://github.com/TBNLZLDYD/ci-context/raw/main/assets/ci-context-marketing.gif
More verified outputs: https://github.com/TBNLZLDYD/ci-context/blob/main/EXAMPLES.md
Why I built it: https://github.com/TBNLZLDYD/ci-context

Two things I'd genuinely like your take on:
1. Does the six-section shape match how you debug a failed run — or is a section
   missing?
2. Does "no AI" read as a feature or a gap to you?
#!/usr/bin/env python3
"""Render the blog/ markdown sources into a static site under blog/_site.

Pure stdlib, deterministic: same sources -> same HTML every run. Run locally to
preview (`python blog/build.py`) or from the pages workflow under Python 3.11.
Why hand-rolled instead of Jekyll: no local Ruby toolchain, fully readable
output, and the site's only source is the plain markdown in blog/_posts.
"""

from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "_site"
POSTS = ROOT / "_posts"

# GitHub Pages serves the repo as https://<user>.github.io/<repo>/, so every
# absolute asset/link needs this prefix. If the site ever moves to a custom
# domain or a user site (/), change this single knob and re-run.
BASE_PATH = "/ci-context"

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="__BASEPATH__/assets/style.css">
</head>
<body>
<div class="wrap">
__BACK__
<header class="site">
<h1>ci-context blog</h1>
<div class="sub">A deterministic, zero-AI CLI that explains a failed CI run &mdash;
one command, six sections of context.</div>
</header>
__CONTENT__
<footer>
Markdown sources in <code>blog/_posts/</code> &middot;
repo <a href="https://github.com/TBNLZLDYD/ci-context">github.com/TBNLZLDYD/ci-context</a>
&middot; install <code>pip install ci-context</code>
</footer>
</div>
</body>
</html>
"""


def inline(text: str) -> str:
    """Convert inline markdown (code, bold, italic, links) to HTML.

    Escape HTML first so raw bytes can't be read as markup, then swap in the
    safer constructs. Backtick spans are replaced with a `%%n%%` sentinel and
    restored last, so marker characters inside a code span survive unchanged
    (e.g. `*not italic*` or `**bold**` kept literal).
    """
    text = html.escape(text, quote=False)
    spans: list[str] = []

    def _hold(m: re.Match[str]) -> str:
        spans.append(m.group(1))
        return f"%%{len(spans) - 1}%%"

    text = re.sub(r"`([^`]+)`", _hold, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)

    def _restore(m: re.Match[str]) -> str:
        return "<code>" + spans[int(m.group(1))] + "</code>"

    return re.sub(r"%%(\d+)%%", _restore, text)


def extract_frontmatter(raw: str) -> dict[str, str]:
    """Pull leading YAML-ish frontmatter into a dict; ignore unknown keys."""
    m = FRONTMATTER.match(raw)
    if not m:
        return {}
    meta: dict[str, str] = {}
    for key, _, value in (line.partition(":") for line in m.group(1).splitlines()):
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


def strip_frontmatter(raw: str) -> str:
    m = FRONTMATTER.match(raw)
    return raw[m.end():] if m else raw


def blocks(raw: str) -> str:
    """Convert the markdown body into HTML, line by line."""
    out: list[str] = []
    lines = raw.splitlines()
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]

        if line.startswith("```"):
            code: list[str] = []
            i += 1
            while i < n and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            continue

        if line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
            i += 1
            continue

        if line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
            i += 1
            continue

        if line.startswith("- "):
            items: list[str] = []
            while i < n and lines[i].startswith("- "):
                items.append("<li>" + inline(lines[i][2:]) + "</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i]):
                body = re.match(r"^\d+\.\s+(.*)$", lines[i]).group(1)
                items.append("<li>" + inline(body) + "</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue

        if line.strip() == "":
            i += 1
            continue

        # Plain paragraph: accumulate until a blank or block-level line.
        para: list[str] = []
        while (
            i < n
            and lines[i].strip() != ""
            and not lines[i].startswith(("```", "## ", "### ", "- "))
            and not re.match(r"^\d+\.\s+", lines[i])
        ):
            para.append(lines[i])
            i += 1
        out.append("<p>" + inline(" ".join(p.strip() for p in para)) + "</p>")

    return "\n".join(out)


def render(post: dict[str, str], slug: str) -> str:
    back = f'<a class="back" href="{BASE_PATH}/">&larr; all posts</a>'
    return (
        TEMPLATE.replace("__TITLE__", html.escape(post["title"]))
        .replace("__BASEPATH__", BASE_PATH)
        .replace("__BACK__", back)
        .replace("__CONTENT__", blocks(post["_body"]))
    )


def render_index(posts: list[tuple[str, dict[str, str]]]) -> str:
    items = [
        '<li><h2><a href="%s/posts/%s.html">%s</a></h2>'
        '<time>%s</time><p>%s</p></li>'
        % (BASE_PATH, slug, html.escape(p["title"]), p.get("date", ""), p["_excerpt"])
        for slug, p in posts
    ]
    content = '<ul class="posts">' + "\n".join(items) + "</ul>"
    return (
        TEMPLATE.replace("__TITLE__", "ci-context blog")
        .replace("__BASEPATH__", BASE_PATH)
        .replace("__BACK__", "")
        .replace("__CONTENT__", content)
    )


def excerpt(body: str) -> str:
    """First non-heading, non-fence paragraph, trimmed to a one-line teaser."""
    paras = [
        b for b in body.split("\n\n")
        if b.strip() and not b.strip().startswith(("#", "`"))
    ]
    if not paras:
        return ""
    first = paras[0].replace("\n", " ").strip()
    return html.escape(first[:160]) + ("&hellip;" if len(first) > 160 else "")


def main() -> None:
    # Wipe first so stale posts never linger in whatever gets deployed.
    shutil.rmtree(SITE, ignore_errors=True)
    (SITE / "posts").mkdir(parents=True)
    (SITE / "assets").mkdir(parents=True, exist_ok=True)

    posts: list[tuple[str, dict[str, str]]] = []
    for path in sorted(POSTS.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta = extract_frontmatter(raw)
        meta["_body"] = strip_frontmatter(raw)
        meta.setdefault("title", path.stem)
        meta["_excerpt"] = excerpt(meta["_body"])

        m = re.match(r"^\d{4}-\d{2}-\d{2}-(.*)$", path.stem)
        slug = m.group(1) if m else path.stem

        (SITE / "posts" / f"{slug}.html").write_text(
            render(meta, slug), encoding="utf-8"
        )
        posts.append((slug, meta))

    (SITE / "index.html").write_text(render_index(posts), encoding="utf-8")
    shutil.copyfile(ROOT / "assets" / "style.css", SITE / "assets" / "style.css")
    (SITE / ".nojekyll").touch()
    print(f"built {len(posts)} post(s) to {SITE}")


if __name__ == "__main__":
    main()
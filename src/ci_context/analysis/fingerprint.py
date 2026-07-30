"""Error fingerprint computation — normalize and hash errors for history matching.

Two errors that differ only in variable values (line numbers, file paths, commit
SHAs) should produce the same fingerprint so the matcher can flag them as
recurring across runs.  The pipeline: normalize the combined error_type + message
string, then SHA-256 hash and truncate to 16 hex chars.
"""

from __future__ import annotations

import hashlib
import re

from ci_context.models.error import ExtractedError

# ---------------------------------------------------------------------------
# Precompiled normalization patterns — applied in strict order
# ---------------------------------------------------------------------------

# 1) Git SHAs: 7-40 consecutive hex chars that are standalone tokens (not
#    embedded inside longer identifiers).  We require the hex run to be bounded
#    by non-hex chars or string edges — this prevents "abc123def" (a mixed
#    alphanumeric identifier) from matching, while still catching true SHAs
#    like "abc1234" that appear as standalone tokens.
_SHA_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{7,40}(?![0-9a-fA-F])")

# 2) File paths — we replace the entire directory portion with <ROOT>/ and
#    keep only the filename plus any trailing :line(:col) suffix.
#    Strategy: match the full path (directory + filename + suffix), then
#    reconstruct with <ROOT>/ replacing the directory part.

# POSIX absolute: /dir/dir/file.ext or /dir/dir/file.ext:line or :line:col
# Greedy match on directory so we replace the entire prefix, not just the
# first segment.  The filename is the last segment before :digits or end.
_POSIX_PATH_RE = re.compile(
    r"(\s|^|:)"                        # boundary (group 1)
    r"(/(?:[^\s:/]+/)+)"               # directory: /seg/seg/ (group 2, greedy)
    r"([\w.\-]+(?:\.\w+)?)"            # filename (group 3)
    r"((?::\d+)*)"                      # optional :line(:col) (group 4)
)

# Windows absolute: C:\dir\dir\file.ext(:digits)*
# Backslash-separated; match directory greedily up to the last backslash.
_WIN_PATH_RE = re.compile(
    r"(\s|^|:)"                        # boundary (group 1)
    r"([A-Za-z]:\\(?:[^\s:\\]+\\)+)"   # directory: C:\seg\seg\ (group 2)
    r"([\w.\-]+(?:\.\w+)?)"            # filename (group 3)
    r"((?::\d+)*)"                      # optional :line(:col) (group 4)
)

# Relative with ./ prefix or multi-segment relative (dir/file.ext).
# Single-segment names ("main.py") are left alone — ambiguous with commands.
_REL_PATH_RE = re.compile(
    r"(\s|^|:)"                        # boundary (group 1)
    r"((?:\./|(?:[\w.\-]+/)+))"        # ./ or seg/seg/ (group 2, greedy)
    r"([\w.\-]+(?:\.\w+)?)"            # filename (group 3)
    r"((?::\d+)*)"                      # optional :line(:col) (group 4)
)

# 3) Standalone decimal numbers — NOT inside words and NOT part of a hex "0x"
#    prefix.  The negative lookbehind/lookahead ensure we don't break identifiers
#    like "utf8" or "python3" while still catching "line 42" -> "line <NUM>".
_NUM_RE = re.compile(r"(?<!\w)(?<!0x)\d+(?!\w)")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _replace_shas(text: str) -> str:
    """Replace standalone hex strings of 7-40 chars with <SHA>."""
    return _SHA_RE.sub("<SHA>", text)


def _replace_paths(text: str) -> str:
    """Replace directory portions of file paths with <ROOT>/.

    Order matters: Windows paths first (drive-letter prefix is unambiguous),
    then POSIX absolute, then relative.  Each replacement keeps the boundary
    character, replaces the directory with <ROOT>/, and preserves the filename
    and any trailing :line(:col) suffix.
    """
    # Windows absolute
    text = _WIN_PATH_RE.sub(r"\1<ROOT>/\3\4", text)
    # POSIX absolute
    text = _POSIX_PATH_RE.sub(r"\1<ROOT>/\3\4", text)
    # Relative (./prefix or multi-segment)
    text = _REL_PATH_RE.sub(r"\1<ROOT>/\3\4", text)
    return text


def _replace_numbers(text: str) -> str:
    """Replace standalone decimal numbers with <NUM>."""
    return _NUM_RE.sub("<NUM>", text)


def _normalize_message(message: str) -> str:
    """Apply normalization rules to an error message string.

    Order is critical: SHAs first (so hex digits inside a SHA aren't later
    treated as standalone numbers), then paths (so path segments aren't
    partially mangled by number replacement), then numbers, then lowercase.
    """
    text = _replace_shas(message)
    text = _replace_paths(text)
    text = _replace_numbers(text)
    return text.lower()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_fingerprint(error: ExtractedError) -> str:
    """Compute a stable fingerprint for an extracted error.

    Same error type + normalized message produces the same fingerprint,
    regardless of variable values (line numbers, paths, SHAs) that differ
    across runs.

    Returns:
        First 16 hex characters of the SHA-256 digest.
    """
    normalized = _normalize_message(f"{error.error_type}: {error.message}")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

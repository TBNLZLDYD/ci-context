"""Log normalization — strip ANSI codes, timestamps, and other noise from CI logs."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Precompiled regex patterns
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Matches GHA timestamp prefixes: with/without microseconds, with Z or offset
GHA_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})\s+")
SECTION_RE = re.compile(r"^##\[section\]")
GROUP_RE = re.compile(r"^::group::")
ENDGROUP_RE = re.compile(r"^::endgroup::")


@dataclass
class NormalizedLine:
    """A single normalized log line with its original line number preserved."""

    original_line_number: int
    content: str


def normalize(raw_log: str) -> list[NormalizedLine]:
    """
    Log normalization pipeline.

    Processing steps:
    1. Split by newlines
    2. Remove ANSI escape codes (e.g., \\x1b[...m)
    3. Remove GHA timestamp prefixes (e.g., 2026-07-16T10:30:00.1234567Z)
    4. Remove ##[section] markers and their lines
    5. Remove ::group:: / ::endgroup:: markers and their lines
    6. Collapse consecutive blank lines to 1 line
    7. Preserve original line number mapping

    Args:
        raw_log: Raw log text

    Returns:
        List of NormalizedLine (blank lines are collapsed but still preserved)
    """
    lines = raw_log.split("\n")
    result: list[NormalizedLine] = []
    last_was_empty = False

    for idx, line in enumerate(lines, start=1):
        # 1. Remove ANSI escape codes
        line = ANSI_RE.sub("", line)

        # 2. Remove GHA timestamp prefixes
        line = GHA_TIMESTAMP_RE.sub("", line)

        # 3. Skip ##[section] marker lines entirely
        if SECTION_RE.match(line):
            continue

        # 4. Remove ::group:: markers (skip the line)
        if GROUP_RE.match(line):
            continue
        if ENDGROUP_RE.match(line):
            continue

        # 5. Collapse consecutive blank lines
        is_empty = not line.strip()
        if is_empty and last_was_empty:
            continue  # Skip consecutive blank lines
        last_was_empty = is_empty

        result.append(NormalizedLine(original_line_number=idx, content=line))

    return result


def normalize_to_text(raw_log: str) -> str:
    """
    Convenience function: normalize() then join back to plain text.

    Args:
        raw_log: Raw log text

    Returns:
        Normalized plain text
    """
    normalized = normalize(raw_log)
    return "\n".join(line.content for line in normalized)

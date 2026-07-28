"""Error extraction engine — extract structured errors from CI log text.

Scans normalized log lines tail-first so the most recent errors surface first.
Uses ErrorPattern definitions to detect block boundaries, extract messages and
locations, then deduplicates by (error_type, message) and assigns confidence
levels before capping the result at 10 entries.
"""

from __future__ import annotations

from ci_context.analysis.patterns import ErrorPattern, get_patterns
from ci_context.models.error import ExtractedError

# Maximum number of distinct errors to return — prevents runaway output on
# logs that contain hundreds of repeated failures.
_MAX_ERRORS = 10

# Maximum raw_lines preserved per error — enough context for display without
# bloating the report.
_MAX_RAW_LINES = 5


def _collect_block(lines: list[str], start_idx: int, end_condition: str) -> str:
    """Collect the error block starting at *start_idx* according to *end_condition*.

    - ``"blank_line"``: include lines until a blank line is encountered.
    - ``"next_start"``: only the start line itself (single-line errors).
    - ``"eof"``: include everything from start to the end of the log.
    """
    if end_condition == "next_start":
        return lines[start_idx]

    if end_condition == "eof":
        return "\n".join(lines[start_idx:])

    # end_condition == "blank_line"
    block_lines: list[str] = []
    for i in range(start_idx, len(lines)):
        # A blank line terminates the block (but is not included).
        if i > start_idx and not lines[i].strip():
            break
        block_lines.append(lines[i])
    return "\n".join(block_lines)


def _assign_confidence(
    pattern: ErrorPattern,
    message: str,
    location: str | None,
    block: str,
) -> str:
    """Determine confidence level for an extracted error.

    - **high**: both message and location were extracted successfully.
    - **medium**: message extracted but no location (pattern has no location
      regex, or the regex didn't match).
    - **low**: message extraction fell back to the last line of the block,
      meaning the dedicated message regex didn't match.
    """
    if location is not None:
        return "high"

    # Detect fallback: extract_message returns the last non-empty line when
    # the message regex fails. Compare the extracted message against the last
    # non-empty line of the block to identify this case.
    if pattern.location_pattern is None:
        # Pattern never produces a location — medium is the ceiling.
        # Check whether the message came from the regex or the fallback.
        if pattern.message_pattern.search(block):
            return "medium"
        return "low"

    # Pattern *can* produce a location but didn't match — medium if the
    # message regex succeeded, low otherwise.
    if pattern.message_pattern.search(block):
        return "medium"
    return "low"


def extract_errors(log: str, language: str | None = None) -> list[ExtractedError]:
    """Extract structured errors from normalized log text.

    Args:
        log: Normalized log text (plain string, newlines separate lines).
        language: Optional language filter passed to the pattern registry so
            only relevant patterns are applied (e.g. ``"python"``).

    Returns:
        Deduplicated list of ExtractedError, most-recent-first, capped at 10.
    """
    if not log.strip():
        return []

    lines = log.split("\n")
    patterns = get_patterns(language)
    if not patterns:
        return []

    # Scan tail-first so later errors are discovered first. The final list
    # preserves this order (most recent error at index 0).
    seen: dict[tuple[str, str], ExtractedError] = {}
    # Track which line indices have already been consumed by a block to avoid
    # overlapping matches (e.g. a Traceback block that contains an ImportError
    # line should not produce a separate ImportError entry for that same line).
    consumed: set[int] = set()

    for idx in range(len(lines) - 1, -1, -1):
        if idx in consumed:
            continue

        line = lines[idx]
        # Try each pattern; first match wins for this line.
        for pattern in patterns:
            if not pattern.matches_start(line):
                continue

            block = _collect_block(lines, idx, pattern.end_condition)
            message = pattern.extract_message(block)
            location = pattern.extract_location(block)
            confidence = _assign_confidence(pattern, message, location, block)

            # Mark block lines as consumed so nested/overlapping patterns
            # don't double-count them.
            block_line_count = block.count("\n") + 1
            for ci in range(idx, min(idx + block_line_count, len(lines))):
                consumed.add(ci)

            # Raw lines: first N lines of the block for display context.
            raw = block.split("\n")[:_MAX_RAW_LINES]

            dedup_key = (pattern.name, message)
            if dedup_key in seen:
                # Same error type + message already found at a later position
                # — increment count rather than adding a duplicate.
                seen[dedup_key].occurrence_count += 1
            else:
                seen[dedup_key] = ExtractedError(
                    error_type=pattern.name,
                    message=message,
                    file_location=location,
                    confidence=confidence,
                    raw_lines=raw,
                    occurrence_count=1,
                )

            # Only one pattern per line — move to the next line index.
            break

    # Preserve tail-first discovery order.
    results = list(seen.values())
    return results[:_MAX_ERRORS]

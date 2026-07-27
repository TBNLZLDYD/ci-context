"""Error pattern definitions — regex rules for known error formats across languages."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ErrorPattern:
    """A single error pattern definition with regex rules for block extraction.

    Each pattern describes how to detect, extract, and delimit a particular
    error format (e.g. Python Traceback, pytest FAILED). The extractor walks
    normalized log lines, uses ``start_pattern`` to find block boundaries, then
    calls ``extract_message`` / ``extract_location`` on the captured block.
    """

    name: str  # Human-readable name, e.g. "Python Traceback"
    language: str  # "python", "node", "go", "java", "shell", "generic"
    start_pattern: re.Pattern[str]  # Detects the start of an error block
    message_pattern: re.Pattern[str]  # Extracts the core error message
    location_pattern: re.Pattern[str] | None  # Extracts file:line location
    # "blank_line" | "next_start" | "eof" — how the error block ends.
    # The extractor uses this to know where to stop collecting lines.
    end_condition: str

    def matches_start(self, line: str) -> bool:
        """Check if a line starts a new error block."""
        return bool(self.start_pattern.search(line))

    def extract_message(self, block: str) -> str:
        """Extract the core error message from a matched block.

        Falls back to the last non-empty line when the message regex doesn't
        match — most error formats put the summary on the final line.
        """
        m = self.message_pattern.search(block)
        if m:
            return m.group(1).strip()
        # Last-resort: take the last non-empty line as the message
        lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        return lines[-1] if lines else ""

    def extract_location(self, block: str) -> str | None:
        """Extract file:line location from a matched block, if present."""
        if self.location_pattern is None:
            return None
        m = self.location_pattern.search(block)
        if not m:
            return None
        # Patterns with 2 groups (file + line) are formatted as "file:line";
        # single-group patterns return the group value directly.
        if m.lastindex == 2:
            return f"{m.group(1)}:{m.group(2)}"
        return m.group(1)


# ---------------------------------------------------------------------------
# Global registry — language-keyed so callers can filter efficiently
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, list[ErrorPattern]] = {}


def register(pattern: ErrorPattern) -> None:
    """Register an error pattern to the global registry."""
    _PATTERNS.setdefault(pattern.language, []).append(pattern)


def get_patterns(language: str | None = None) -> list[ErrorPattern]:
    """Get registered patterns, optionally filtered by language."""
    if language is None:
        return [p for patterns in _PATTERNS.values() for p in patterns]
    return _PATTERNS.get(language, [])


# ---------------------------------------------------------------------------
# Built-in Python error patterns
# ---------------------------------------------------------------------------

# Python Traceback: multi-line block ending at a blank line.
# The message regex targets the final "ErrorType: message" line; the location
# regex captures the *first* File+line pair (the call site closest to the
# failure), formatted as "file:line".
register(
    ErrorPattern(
        name="Python Traceback",
        language="python",
        start_pattern=re.compile(r"^Traceback \(most recent call last\):"),
        message_pattern=re.compile(r"^\w*(?:Error|Exception):\s*(.+)$", re.MULTILINE),
        location_pattern=re.compile(r'File "(.+?)", line (\d+)'),
        end_condition="blank_line",
    )
)

# pytest FAILED: one-line summary per failing test.
# The test path (e.g. "tests/test_foo.py::test_bar") serves as both the
# location and the primary identifier; the optional "- message" suffix is
# the error detail.
register(
    ErrorPattern(
        name="FAILED (pytest)",
        language="python",
        start_pattern=re.compile(r"^FAILED\s+"),
        message_pattern=re.compile(r"^FAILED\s+(.+?)(?:\s*-|$)", re.MULTILINE),
        location_pattern=re.compile(r"FAILED\s+(\S+)"),
        end_condition="next_start",
    )
)

# ModuleNotFoundError: single-line error with no file location.
# Common in CI when a dependency is missing from the environment.
register(
    ErrorPattern(
        name="ModuleNotFoundError",
        language="python",
        start_pattern=re.compile(r"^ModuleNotFoundError:"),
        message_pattern=re.compile(r"^ModuleNotFoundError:\s*(.+)$", re.MULTILINE),
        location_pattern=None,
        end_condition="next_start",
    )
)

# ImportError: single-line error with no file location.
# Broader than ModuleNotFoundError; covers cyclic imports, bad names, etc.
register(
    ErrorPattern(
        name="ImportError",
        language="python",
        start_pattern=re.compile(r"^ImportError:"),
        message_pattern=re.compile(r"^ImportError:\s*(.+)$", re.MULTILINE),
        location_pattern=None,
        end_condition="next_start",
    )
)

# ---------------------------------------------------------------------------
# Built-in Node.js error patterns
# ---------------------------------------------------------------------------

# Node Error + stack: multi-line block ending at a blank line.
# The optional "Uncaught " prefix covers both caught and uncaught throws;
# the location regex targets the first stack frame with a parenthesised
# source location (e.g. "at Object.<anonymous> (/path/file.js:10:25)"),
# formatted as "file:line" via the 2-group extraction logic.
register(
    ErrorPattern(
        name="Error + stack (Node.js)",
        language="node",
        start_pattern=re.compile(r"^(?:Uncaught )?Error:"),
        message_pattern=re.compile(
            r"^(?:Uncaught )?Error:\s*(.+)$", re.MULTILINE
        ),
        location_pattern=re.compile(r"at .+?\((.+?):(\d+):\d+\)"),
        end_condition="blank_line",
    )
)

# Jest FAIL: one-line summary per failing test suite.
# Jest prints "FAIL <test-path>" on its own line; the test path doubles as
# the location since Jest doesn't emit file:line for individual assertions.
register(
    ErrorPattern(
        name="Jest FAIL",
        language="node",
        start_pattern=re.compile(r"^\s*FAIL\s+"),
        message_pattern=re.compile(r"^\s*FAIL\s+(.+)$", re.MULTILINE),
        location_pattern=re.compile(r"^\s*FAIL\s+(\S+)"),
        end_condition="next_start",
    )
)

# npm ERR!: one-line errors from the npm CLI.
# No file:line location exists — npm errors are command-level diagnostics
# (e.g. missing scripts, network failures), not source-level.
register(
    ErrorPattern(
        name="npm ERR!",
        language="node",
        start_pattern=re.compile(r"^npm ERR!\s+"),
        message_pattern=re.compile(r"^npm ERR!\s+(.+)$", re.MULTILINE),
        location_pattern=None,
        end_condition="next_start",
    )
)

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
    # "blank_line" | "next_start" | "eof" | "fixed_lines" — how the error
    # block ends. The extractor uses this to know where to stop collecting lines.
    end_condition: str
    # When end_condition == "fixed_lines", collect exactly this many lines
    # from the start. Useful for formats where the line after the start
    # contains the location (e.g. ruff's "--> file:line:col" arrow line).
    block_size: int = 1

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

# ruff linter output: two-line block (message line + "-->" location line).
# ruff prints the rule code + message on the first line, then a "--> file:line:col"
# arrow on the second line pointing to the offending code.
# Rule codes are 1+ uppercase letters + digits (E501, UP045, PLW0120, etc.).
register(
    ErrorPattern(
        name="Ruff lint error",
        language="python",
        start_pattern=re.compile(r"^[A-Z]+\d+\s.*"),
        message_pattern=re.compile(r"^([A-Z]+\d+\s.+)$", re.MULTILINE),
        location_pattern=re.compile(r"-->\s*(.+?):(\d+):\d+"),
        end_condition="fixed_lines",
        block_size=2,
    )
)

# mypy type error: single-line with "file:line: error:" prefix.
# mypy emits "src/module.py:42: error: Incompatible types ..." on one line,
# so the location is embedded in the start line itself.
register(
    ErrorPattern(
        name="mypy type error",
        language="python",
        start_pattern=re.compile(r"^\S+\.py:\d+:\s*error:"),
        message_pattern=re.compile(
            r"^\S+\.py:\d+:\s*error:\s*(.+)$", re.MULTILINE
        ),
        location_pattern=re.compile(r"(\S+\.py):(\d+):\s*error:"),
        end_condition="next_start",
    )
)

# ---------------------------------------------------------------------------
# Built-in Go error patterns
# ---------------------------------------------------------------------------

# Go panic: multi-line block ending at a blank line.
# A panic prints "panic: <message>" then a goroutine stack; the first stack
# frame (e.g. "/path/file.go:42 +0xabc") gives the crash location.
register(
    ErrorPattern(
        name="Go panic",
        language="go",
        start_pattern=re.compile(r"^panic:"),
        message_pattern=re.compile(r"^panic:\s*(.+)$", re.MULTILINE),
        location_pattern=re.compile(r"(.+\.go):(\d+)\s+\+0x[0-9a-fA-F]+"),
        end_condition="blank_line",
    )
)

# Go build/compile error: single-line with file:line:col prefix.
# The Go compiler emits "./main.go:10:2: syntax error" — the location is
# embedded in the line itself, so end_condition="next_start" (no multi-line
# block to collect).
register(
    ErrorPattern(
        name="Go build error",
        language="go",
        start_pattern=re.compile(r"^(?:\./)?\S+\.go:\d+:\d+:"),
        message_pattern=re.compile(
            r"^(?:\./)?\S+\.go:\d+:\d+:\s*(.+)$", re.MULTILINE
        ),
        location_pattern=re.compile(r"((?:\./)?\S+\.go):(\d+):\d+:"),
        end_condition="next_start",
    )
)

# ---------------------------------------------------------------------------
# Built-in Java error patterns
# ---------------------------------------------------------------------------

# Java Exception: multi-line block ending at a blank line.
# A Java stack trace starts with "fully.qualified.Exception: message";
# the first "at" frame with a source file (e.g. "at Class.method(File.java:42)")
# provides the location.
register(
    ErrorPattern(
        name="Java Exception",
        language="java",
        start_pattern=re.compile(
            r"^[\w.$]+(?:Exception|Error|Throwable):\s"
        ),
        message_pattern=re.compile(
            r"^([\w.$]+(?:Exception|Error|Throwable)):\s*(.+)$",
            re.MULTILINE,
        ),
        location_pattern=re.compile(r"at\s+[\w.$]+\.\w+\(([\w.]+):(\d+)\)"),
        end_condition="blank_line",
    )
)

# Java compilation error: single-line with file:line prefix.
# javac prints "File.java:10: error: ';' expected" — the location is the
# file:line before the "error:" keyword.
register(
    ErrorPattern(
        name="Java compilation error",
        language="java",
        start_pattern=re.compile(r"^\S+\.java:\d+:\s*error:"),
        message_pattern=re.compile(
            r"^\S+\.java:\d+:\s*error:\s*(.+)$", re.MULTILINE
        ),
        location_pattern=re.compile(r"(\S+\.java):(\d+):\s*error:"),
        end_condition="next_start",
    )
)

# ---------------------------------------------------------------------------
# Built-in Shell/generic error patterns
# ---------------------------------------------------------------------------

# Shell exit code: one-line diagnostic from CI wrappers or shell scripts.
# Covers both "Error: ... exited with code N" and "Command failed with exit
# code N" phrasing — no file:line location exists for process-level errors.
register(
    ErrorPattern(
        name="Shell exit code",
        language="shell",
        start_pattern=re.compile(
            r"(?:Error|Command failed).*exited with code \d+"
        ),
        message_pattern=re.compile(
            r"((?:Error|Command failed).*exited with code \d+)", re.MULTILINE
        ),
        location_pattern=None,
        end_condition="next_start",
    )
)

# Makefile error: one-line from GNU Make when a recipe exits non-zero.
# The bracketed target (e.g. "[target]") and error code identify the failure;
# no source location is available at the Make level.
register(
    ErrorPattern(
        name="Makefile error",
        language="shell",
        start_pattern=re.compile(r"make(?:\[\d+\])?:\s*\*\*\*"),
        message_pattern=re.compile(
            r"make(?:\[\d+\])?:\s*\*\*\*\s*(.+)$", re.MULTILINE
        ),
        location_pattern=None,
        end_condition="next_start",
    )
)

# Docker/buildkit ERROR: one-line from Docker builds.
# Docker build output prefixes errors with "ERROR:"; the text after the
# prefix is the diagnostic (e.g. "failed to fetch", "process did not run").
register(
    ErrorPattern(
        name="Docker error",
        language="generic",
        start_pattern=re.compile(r"^ERROR:\s+"),
        message_pattern=re.compile(r"^ERROR:\s+(.+)$", re.MULTILINE),
        location_pattern=None,
        end_condition="next_start",
    )
)

# Permission denied: common shell/OS error when a file or socket is
# inaccessible. The path is embedded in the message; no separate location.
register(
    ErrorPattern(
        name="Permission denied",
        language="shell",
        start_pattern=re.compile(r"Permission denied"),
        message_pattern=re.compile(r"Permission denied\s*(.+?)$", re.MULTILINE),
        location_pattern=None,
        end_condition="next_start",
    )
)

# Command not found: shell diagnostic when an executable is missing from
# PATH. The command name is the key identifier; no file:line exists.
register(
    ErrorPattern(
        name="Command not found",
        language="shell",
        start_pattern=re.compile(
            r"(?:bash|sh|zsh):\s+\S+:\s*(?:command\s+)?not found"
        ),
        message_pattern=re.compile(
            r"(?:bash|sh|zsh):\s+(\S+):\s*(?:command\s+)?not found",
            re.MULTILINE,
        ),
        location_pattern=None,
        end_condition="next_start",
    )
)

# Segmentation fault: OS-level signal when a process accesses invalid memory.
# The optional "(core dumped)" suffix is informational; no source location
# is available from the signal itself.
register(
    ErrorPattern(
        name="Segmentation fault",
        language="generic",
        start_pattern=re.compile(r"Segmentation fault"),
        message_pattern=re.compile(r"(Segmentation fault(?:\s*\(core dumped\))?)"),
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

# npm ERR!: actionable npm errors only.
# npm prints ~15 lines of cascade noise per failure. We filter to only
# lines that carry real diagnostics: "Failed at", "missing", "command failed"
# and error messages. Pure metadata (errno, code, Exit status, argv,
# Linux, node v, etc.) is excluded via positive matching of diagnostic keywords.
register(
    ErrorPattern(
        name="npm ERR!",
        language="node",
        start_pattern=re.compile(
            r"^npm ERR!\s+(?:Failed at|missing|Command failed|command not found)"
        ),
        message_pattern=re.compile(r"^npm ERR!\s+(.+)$", re.MULTILINE),
        location_pattern=None,
        end_condition="next_start",
    )
)

# eslint lint error: single-line with "line:col  error  message" format.
# eslint prints position columns, the severity ("error" or "warning"), then
# the rule name in parentheses. We only match "error" severity (not warnings).
register(
    ErrorPattern(
        name="eslint error",
        language="node",
        start_pattern=re.compile(r"^\s*\d+:\d+\s+error\s+"),
        message_pattern=re.compile(
            r"^\s*\d+:\d+\s+error\s+(.+)$", re.MULTILINE
        ),
        location_pattern=None,
        end_condition="next_start",
    )
)

# jscs code style error: single-line summary from the JavaScript Code Style
# checker. jscs prints "N code style error(s) found." as a final summary.
register(
    ErrorPattern(
        name="jscs style error",
        language="node",
        start_pattern=re.compile(r"\d+\s+code style error"),
        message_pattern=re.compile(r"(\d+\s+code style error.+)", re.MULTILINE),
        location_pattern=None,
        end_condition="next_start",
    )
)


# ---------------------------------------------------------------------------
# Built-in generic CI/git error patterns
# ---------------------------------------------------------------------------

# GHA "Process completed with exit code N": the universal step-failure marker.
# After normalization strips the "##[error]" prefix, only the message remains.
# This catches it as a last-resort generic pattern so a non-zero exit is never
# silently dropped, even when no language-specific pattern matched.
register(
    ErrorPattern(
        name="GHA exit code",
        language="generic",
        start_pattern=re.compile(r"Process completed with exit code \d+"),
        message_pattern=re.compile(
            r"(Process completed with exit code \d+)", re.MULTILINE
        ),
        location_pattern=None,
        end_condition="next_start",
    )
)

# git fatal: single-line errors from git operations (clone, fetch, checkout).
# Common in CI when SSL certificates are misconfigured, repos are private, or
# refs are missing. The message after "fatal:" is the diagnostic.
register(
    ErrorPattern(
        name="git fatal",
        language="generic",
        start_pattern=re.compile(r"^fatal:\s+"),
        message_pattern=re.compile(r"^fatal:\s*(.+)$", re.MULTILINE),
        location_pattern=None,
        end_condition="next_start",
    )
)

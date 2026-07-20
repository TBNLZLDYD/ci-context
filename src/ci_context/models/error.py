"""ExtractedError data model."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractedError:
    """
    A single error extracted from CI log text.

    Produced by the error extraction engine (analysis/extractor.py) and consumed
    by the renderers and the fingerprint/matcher modules.
    """

    error_type: str  # "Python Traceback", "npm Error", "Go panic", etc.
    message: str  # Core error message
    file_location: str | None = None  # "src/main.py:42" or None
    confidence: str = "medium"  # "high" | "medium" | "low"
    raw_lines: list[str] = field(default_factory=list)  # Original log lines (up to 5)
    occurrence_count: int = 1  # How many times this error appeared in the run
    step_name: str = ""  # Which step produced this error

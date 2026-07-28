"""Tests for the error extraction engine (analysis/extractor.py).

Uses realistic fixture logs that mimic GitHub Actions output after the
normalizer has stripped timestamps and ANSI codes. Each test class covers
one language family or edge-case category.
"""

from __future__ import annotations

import pathlib
import unittest

from ci_context.analysis.extractor import extract_errors

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TestPythonTraceback(unittest.TestCase):
    """Python-specific error patterns: Traceback, ModuleNotFoundError, pytest."""

    def test_extracts_traceback_with_location(self) -> None:
        """Python Traceback yields error_type, message, file_location, confidence=high."""
        log = _load_fixture("python_traceback.log")
        errors = extract_errors(log, language="python")

        tracebacks = [e for e in errors if e.error_type == "Python Traceback"]
        self.assertGreaterEqual(len(tracebacks), 1)

        tb = tracebacks[0]
        self.assertIn("invalid literal for int()", tb.message)
        # Location comes from the first File+line pair in the block.
        self.assertEqual(tb.file_location, "src/parser.py:42")
        self.assertEqual(tb.confidence, "high")

    def test_extracts_module_not_found_error(self) -> None:
        """ModuleNotFoundError extracted with no location, confidence=medium."""
        log = _load_fixture("python_traceback.log")
        errors = extract_errors(log, language="python")

        mod_errs = [e for e in errors if e.error_type == "ModuleNotFoundError"]
        self.assertEqual(len(mod_errs), 1)

        err = mod_errs[0]
        self.assertIn("No module named 'requests'", err.message)
        # Pattern has no location_pattern, so ceiling is medium.
        self.assertIsNone(err.file_location)
        self.assertEqual(err.confidence, "medium")

    def test_extracts_pytest_failed(self) -> None:
        """pytest FAILED line extracted with test path as location."""
        log = _load_fixture("python_traceback.log")
        errors = extract_errors(log, language="python")

        failed = [e for e in errors if e.error_type == "FAILED (pytest)"]
        self.assertEqual(len(failed), 1)

        err = failed[0]
        self.assertIn("tests/test_parser.py::test_parse_int", err.message)
        self.assertEqual(err.file_location, "tests/test_parser.py::test_parse_int")

    def test_deduplicates_same_error(self) -> None:
        """Two identical ValueErrors produce one ExtractedError with occurrence_count=2."""
        log = _load_fixture("python_traceback.log")
        errors = extract_errors(log, language="python")

        tracebacks = [e for e in errors if e.error_type == "Python Traceback"]
        # Both Tracebacks in the fixture have the same ValueError message,
        # so they should be deduplicated into a single entry.
        value_errs = [
            t for t in tracebacks if "invalid literal for int()" in t.message
        ]
        self.assertEqual(len(value_errs), 1)
        self.assertEqual(value_errs[0].occurrence_count, 2)

    def test_caps_at_10_errors(self) -> None:
        """Extractor returns at most 10 distinct errors even when more exist."""
        log = _load_fixture("multiple_tracebacks.log")
        errors = extract_errors(log, language="python")
        # The fixture has 12 distinct Tracebacks; only 10 should be returned.
        self.assertLessEqual(len(errors), 10)
        # But we should still have a substantial number extracted.
        self.assertGreaterEqual(len(errors), 8)


class TestNodeErrors(unittest.TestCase):
    """Node.js-specific error patterns: Error+stack, Jest FAIL, npm ERR!."""

    def test_extracts_node_error_with_stack(self) -> None:
        """Node Error+stack extracted with location from the first stack frame."""
        log = _load_fixture("node_errors.log")
        errors = extract_errors(log, language="node")

        node_errs = [e for e in errors if e.error_type == "Error + stack (Node.js)"]
        self.assertEqual(len(node_errs), 1)

        err = node_errs[0]
        self.assertIn("Cannot find module './utils'", err.message)
        # Location from "at Object.<anonymous> (/home/runner/project/src/index.js:10:25)".
        self.assertEqual(err.file_location, "/home/runner/project/src/index.js:10")
        self.assertEqual(err.confidence, "high")

    def test_extracts_jest_fail(self) -> None:
        """Jest FAIL line extracted with test path as location."""
        log = _load_fixture("node_errors.log")
        errors = extract_errors(log, language="node")

        jest = [e for e in errors if e.error_type == "Jest FAIL"]
        self.assertEqual(len(jest), 1)

        err = jest[0]
        self.assertIn("src/__tests__/api.test.js", err.message)
        self.assertEqual(err.file_location, "src/__tests__/api.test.js")

    def test_extracts_npm_err(self) -> None:
        """npm ERR! extracted with no location, confidence=medium."""
        log = _load_fixture("node_errors.log")
        errors = extract_errors(log, language="node")

        npm_errs = [e for e in errors if e.error_type == "npm ERR!"]
        # Multiple npm ERR! lines exist; they may or may not deduplicate
        # depending on message content. At least one must mention "build".
        self.assertGreaterEqual(len(npm_errs), 1)

        # Find the one about the missing build script.
        build_err = [e for e in npm_errs if "build" in e.message]
        self.assertGreaterEqual(len(build_err), 1)
        self.assertIsNone(build_err[0].file_location)
        self.assertEqual(build_err[0].confidence, "medium")


class TestEdgeCases(unittest.TestCase):
    """Boundary conditions: empty input, no errors, language filtering, mixed logs."""

    def test_empty_log_returns_empty(self) -> None:
        """An empty string produces no extracted errors."""
        errors = extract_errors("")
        self.assertEqual(errors, [])

    def test_no_errors_log_returns_empty(self) -> None:
        """A clean log with no error patterns produces no extracted errors."""
        log = _load_fixture("no_errors.log")
        errors = extract_errors(log)
        self.assertEqual(errors, [])

    def test_language_filter(self) -> None:
        """Filtering by language='node' on a Python-only log yields no results."""
        log = _load_fixture("python_traceback.log")
        errors = extract_errors(log, language="node")
        self.assertEqual(errors, [])

    def test_mixed_log_extracts_both(self) -> None:
        """A mixed Python+Node log with language=None yields both language families."""
        log = _load_fixture("mixed_errors.log")
        errors = extract_errors(log)

        types = {e.error_type for e in errors}
        # Should contain at least one Python pattern and one Node pattern.
        python_types = {t for t in types if "Python" in t or "pytest" in t
                        or "ModuleNotFoundError" in t or "ImportError" in t}
        node_types = {t for t in types if "Node" in t or "Jest" in t
                      or "npm" in t}
        self.assertGreaterEqual(len(python_types), 1,
                                "Expected at least one Python error type")
        self.assertGreaterEqual(len(node_types), 1,
                                "Expected at least one Node error type")


if __name__ == "__main__":
    unittest.main()

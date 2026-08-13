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


class TestGoErrors(unittest.TestCase):
    """Go-specific error patterns: panic with stack trace, build/compile errors."""

    def test_extracts_go_panic_with_location(self) -> None:
        """Go panic yields error_type, message, file_location from first stack frame."""
        log = _load_fixture("go_errors.log")
        errors = extract_errors(log, language="go")

        panics = [e for e in errors if e.error_type == "Go panic"]
        self.assertGreaterEqual(len(panics), 1)

        err = panics[0]
        self.assertIn("runtime error: index out of range", err.message)
        # Location from the first .go:line+0x... stack frame in the block.
        self.assertIn("handler.go:87", err.file_location or "")
        self.assertEqual(err.confidence, "high")

    def test_extracts_go_build_error_with_location(self) -> None:
        """Go build error yields error_type, message, file:line location, confidence=high."""
        log = _load_fixture("go_errors.log")
        errors = extract_errors(log, language="go")

        build_errs = [e for e in errors if e.error_type == "Go build error"]
        self.assertGreaterEqual(len(build_errs), 1)

        # The fixture has two distinct build errors; verify at least one.
        err = build_errs[0]
        # Message should contain the compiler diagnostic text.
        self.assertTrue(
            "undefined: someFunc" in err.message
            or "syntax error" in err.message,
            f"Expected build error message, got: {err.message!r}",
        )
        # Build errors embed file:line:col in the line itself.
        self.assertIsNotNone(err.file_location)
        self.assertEqual(err.confidence, "high")


class TestJavaErrors(unittest.TestCase):
    """Java-specific error patterns: Exception with stack trace, compilation errors."""

    def test_extracts_java_exception_with_location(self) -> None:
        """Java Exception yields error_type, class name, file:line location, confidence=high."""
        log = _load_fixture("java_errors.log")
        errors = extract_errors(log, language="java")

        exceptions = [e for e in errors if e.error_type == "Java Exception"]
        self.assertGreaterEqual(len(exceptions), 1)

        err = exceptions[0]
        # extract_message returns group(1) — the fully-qualified exception class.
        self.assertIn("ServiceException", err.message)
        # Location from the first "at" frame with a source file reference.
        self.assertEqual(err.file_location, "DatabaseService.java:42")
        self.assertEqual(err.confidence, "high")

    def test_extracts_java_compilation_error_with_location(self) -> None:
        """Java compilation error yields error_type, message, file:line, confidence=high."""
        log = _load_fixture("java_errors.log")
        errors = extract_errors(log, language="java")

        comp_errs = [e for e in errors if e.error_type == "Java compilation error"]
        self.assertGreaterEqual(len(comp_errs), 1)

        # The fixture has two distinct compilation errors; verify at least one.
        err = comp_errs[0]
        self.assertTrue(
            "';' expected" in err.message
            or "cannot find symbol" in err.message,
            f"Expected compilation error message, got: {err.message!r}",
        )
        # Compilation errors embed file:line before the "error:" keyword.
        self.assertIsNotNone(err.file_location)
        self.assertEqual(err.confidence, "high")


class TestShellErrors(unittest.TestCase):
    """Shell/generic error patterns: exit codes, make, docker, permission, etc."""

    def test_extracts_shell_exit_code(self) -> None:
        """Shell exit code error extracted with no location, confidence=medium."""
        log = _load_fixture("shell_errors.log")
        errors = extract_errors(log)

        exit_errs = [e for e in errors if e.error_type == "Shell exit code"]
        self.assertGreaterEqual(len(exit_errs), 1)

        err = exit_errs[0]
        self.assertIn("exited with code", err.message)
        # Process-level errors have no source file location.
        self.assertIsNone(err.file_location)
        self.assertEqual(err.confidence, "medium")

    def test_extracts_makefile_error(self) -> None:
        """Makefile error extracted with target info, no location, confidence=medium."""
        log = _load_fixture("shell_errors.log")
        errors = extract_errors(log)

        make_errs = [e for e in errors if e.error_type == "Makefile error"]
        self.assertGreaterEqual(len(make_errs), 1)

        err = make_errs[0]
        # Message contains the bracketed target and error code.
        self.assertIn("[build]", err.message)
        self.assertIsNone(err.file_location)
        self.assertEqual(err.confidence, "medium")

    def test_extracts_docker_error(self) -> None:
        """Docker ERROR: line extracted with no location, confidence=medium."""
        log = _load_fixture("shell_errors.log")
        errors = extract_errors(log)

        docker_errs = [e for e in errors if e.error_type == "Docker error"]
        self.assertGreaterEqual(len(docker_errs), 1)

        err = docker_errs[0]
        self.assertIn("failed to solve", err.message)
        self.assertIsNone(err.file_location)
        self.assertEqual(err.confidence, "medium")

    def test_extracts_permission_denied(self) -> None:
        """Permission denied error extracted with no location, confidence=medium."""
        log = _load_fixture("shell_errors.log")
        errors = extract_errors(log)

        perm_errs = [e for e in errors if e.error_type == "Permission denied"]
        self.assertGreaterEqual(len(perm_errs), 1)

        err = perm_errs[0]
        # Message regex captures text after "Permission denied".
        self.assertIn("deploy.sh", err.message)
        self.assertIsNone(err.file_location)
        self.assertEqual(err.confidence, "medium")

    def test_extracts_command_not_found(self) -> None:
        """Command not found error extracted with command name, no location, medium."""
        log = _load_fixture("shell_errors.log")
        errors = extract_errors(log)

        cmd_errs = [e for e in errors if e.error_type == "Command not found"]
        self.assertGreaterEqual(len(cmd_errs), 1)

        err = cmd_errs[0]
        # Message regex group(1) is the command name.
        self.assertEqual(err.message, "somecommand")
        self.assertIsNone(err.file_location)
        self.assertEqual(err.confidence, "medium")

    def test_extracts_segmentation_fault(self) -> None:
        """Segmentation fault extracted with no location, confidence=medium."""
        log = _load_fixture("shell_errors.log")
        errors = extract_errors(log)

        segv_errs = [e for e in errors if e.error_type == "Segmentation fault"]
        self.assertGreaterEqual(len(segv_errs), 1)

        err = segv_errs[0]
        self.assertIn("Segmentation fault", err.message)
        self.assertIsNone(err.file_location)
        self.assertEqual(err.confidence, "medium")


class TestMixedLanguageExtraction(unittest.TestCase):
    """Cross-language extraction: mixed logs and language filtering."""

    def test_mixed_go_java_extracts_both(self) -> None:
        """A log with Go+Java errors yields error types from both languages."""
        # Combine Go and Java fixtures into one virtual log.
        go_log = _load_fixture("go_errors.log")
        java_log = _load_fixture("java_errors.log")
        combined = go_log + "\n" + java_log

        errors = extract_errors(combined)
        types = {e.error_type for e in errors}

        go_types = {t for t in types if "Go" in t}
        java_types = {t for t in types if "Java" in t}
        self.assertGreaterEqual(
            len(go_types), 1, "Expected at least one Go error type",
        )
        self.assertGreaterEqual(
            len(java_types), 1, "Expected at least one Java error type",
        )

    def test_mixed_python_shell_extracts_both(self) -> None:
        """A log with Python+Shell errors yields error types from both families."""
        py_log = _load_fixture("python_traceback.log")
        sh_log = _load_fixture("shell_errors.log")
        combined = py_log + "\n" + sh_log

        errors = extract_errors(combined)
        types = {e.error_type for e in errors}

        py_types = {t for t in types if "Python" in t or "Module" in t
                    or "pytest" in t or "Import" in t}
        sh_types = {t for t in types if "Shell" in t or "Makefile" in t
                    or "Docker" in t or "Permission" in t
                    or "Command" in t or "Segmentation" in t}
        self.assertGreaterEqual(
            len(py_types), 1, "Expected at least one Python error type",
        )
        self.assertGreaterEqual(
            len(sh_types), 1, "Expected at least one Shell/generic error type",
        )

    def test_language_filter_go_only(self) -> None:
        """language='go' on a mixed Go+Java log returns only Go patterns."""
        go_log = _load_fixture("go_errors.log")
        java_log = _load_fixture("java_errors.log")
        combined = go_log + "\n" + java_log

        errors = extract_errors(combined, language="go")
        for err in errors:
            self.assertEqual(
                err.error_type[:2], "Go",
                f"Expected Go error type, got: {err.error_type!r}",
            )

    def test_language_filter_java_only(self) -> None:
        """language='java' on a mixed Go+Java log returns only Java patterns."""
        go_log = _load_fixture("go_errors.log")
        java_log = _load_fixture("java_errors.log")
        combined = go_log + "\n" + java_log

        errors = extract_errors(combined, language="java")
        for err in errors:
            self.assertIn(
                "Java", err.error_type,
                f"Expected Java error type, got: {err.error_type!r}",
            )


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


class TestLinterPatterns(unittest.TestCase):
    """Linter output patterns: ruff, mypy, eslint, jscs."""

    def test_extracts_ruff_lint_error_with_location(self) -> None:
        """Ruff lint error yields message + file:line from the arrow line."""
        log = _load_fixture("python_lint.log")
        errors = extract_errors(log, language="python")

        ruff_errs = [e for e in errors if e.error_type == "Ruff lint error"]
        self.assertGreaterEqual(len(ruff_errs), 1)

        # Tail-first: first extracted is the last in the log. Find E501.
        e501 = [e for e in ruff_errs if "E501" in e.message]
        self.assertGreaterEqual(len(e501), 1)

        err = e501[0]
        self.assertIn("Line too long", err.message)
        # Location from the "--> file:line:col" arrow line.
        self.assertIsNotNone(err.file_location)
        self.assertIn("exceptions.py", err.file_location or "")
        self.assertEqual(err.confidence, "high")

    def test_extracts_multiple_ruff_errors(self) -> None:
        """Multiple distinct ruff violations are each extracted separately."""
        log = _load_fixture("python_lint.log")
        errors = extract_errors(log, language="python")

        ruff_errs = [e for e in errors if e.error_type == "Ruff lint error"]
        # Fixture has 4 ruff errors: E501, UP045, I001, F401
        self.assertGreaterEqual(len(ruff_errs), 3)

    def test_extracts_mypy_type_error(self) -> None:
        """mypy error yields message + file:line from the prefix."""
        log = _load_fixture("python_lint.log")
        errors = extract_errors(log, language="python")

        mypy_errs = [e for e in errors if e.error_type == "mypy type error"]
        self.assertGreaterEqual(len(mypy_errs), 1)

        # Tail-first: check all mypy errors for expected content.
        messages = [e.message for e in mypy_errs]
        self.assertTrue(
            any("return type annotation" in m for m in messages),
            f"Expected 'return type annotation' in mypy messages: {messages}",
        )
        for err in mypy_errs:
            self.assertIsNotNone(err.file_location)
            self.assertEqual(err.confidence, "high")

    def test_extracts_eslint_error(self) -> None:
        """eslint error line yields message, no location, confidence=medium."""
        log = _load_fixture("node_lint.log")
        errors = extract_errors(log, language="node")

        eslint_errs = [e for e in errors if e.error_type == "eslint error"]
        self.assertGreaterEqual(len(eslint_errs), 1)

        # At least one eslint error should mention "Incorrect examples"
        messages = [e.message for e in eslint_errs]
        self.assertTrue(
            any("Incorrect examples" in m for m in messages),
            f"Expected 'Incorrect examples' in eslint messages: {messages}",
        )
        # All eslint errors have no file location and medium confidence.
        for err in eslint_errs:
            self.assertIsNone(err.file_location)
            self.assertEqual(err.confidence, "medium")

    def test_extracts_jscs_style_error(self) -> None:
        """jscs code style summary line is extracted."""
        log = _load_fixture("node_lint.log")
        errors = extract_errors(log, language="node")

        jscs_errs = [e for e in errors if e.error_type == "jscs style error"]
        self.assertEqual(len(jscs_errs), 1)

        err = jscs_errs[0]
        self.assertIn("1 code style error", err.message)

    def test_npm_err_denounced_filters_cascade_noise(self) -> None:
        """npm ERR! pattern excludes cascade-noise lines (errno, Exit status, etc.)."""
        log = _load_fixture("node_lint.log")
        errors = extract_errors(log, language="node")

        npm_errs = [e for e in errors if e.error_type == "npm ERR!"]
        # Only the "Failed at" line should survive; all noise lines excluded.
        messages = [e.message for e in npm_errs]

        # The "Failed at" line must be present.
        failed = [m for m in messages if "Failed at" in m]
        self.assertGreaterEqual(len(failed), 1)

        # Cascade-noise lines must NOT be present.
        for m in messages:
            self.assertNotIn("errno", m, f"errno noise leaked: {m}")
            self.assertNotIn("Exit status", m, f"Exit status noise leaked: {m}")
            self.assertNotIn("complete log", m, f"complete log noise leaked: {m}")
            self.assertNotIn("ELIFECYCLE", m, f"ELIFECYCLE noise leaked: {m}")
            self.assertNotIn("npm argv", m, f"argv noise leaked: {m}")


class TestGenericCIPatterns(unittest.TestCase):
    """Generic CI error patterns: GHA exit code, git fatal."""

    def test_extracts_gha_exit_code(self) -> None:
        """GHA exit code extracted after normalizer strips ##[error] prefix."""
        log = _load_fixture("generic_ci_errors.log")
        # Simulate what normalizer does: strip ##[error] prefix
        from ci_context.analysis.normalizer import normalize_to_text

        normalized = normalize_to_text(log)
        errors = extract_errors(normalized)

        gha_errs = [e for e in errors if e.error_type == "GHA exit code"]
        self.assertGreaterEqual(len(gha_errs), 1)

        err = gha_errs[0]
        self.assertIn("exit code 1", err.message)

    def test_extracts_git_fatal_error(self) -> None:
        """git fatal: line is extracted with the diagnostic message."""
        log = _load_fixture("generic_ci_errors.log")
        from ci_context.analysis.normalizer import normalize_to_text

        normalized = normalize_to_text(log)
        errors = extract_errors(normalized)

        git_errs = [e for e in errors if e.error_type == "git fatal"]
        self.assertGreaterEqual(len(git_errs), 1)

        err = git_errs[0]
        self.assertIn("unable to access", err.message)
        self.assertIn("certificate", err.message)
        self.assertIsNone(err.file_location)
        self.assertEqual(err.confidence, "medium")


class TestNormalizerGhaError(unittest.TestCase):
    """Verify that the normalizer strips ##[error] prefix but keeps content."""

    def test_strips_error_prefix_keeps_content(self) -> None:
        """##[error] prefix is removed, message content is preserved."""
        from ci_context.analysis.normalizer import normalize_to_text

        raw = "##[error]Process completed with exit code 1."
        result = normalize_to_text(raw)
        self.assertEqual(result, "Process completed with exit code 1.")

    def test_strips_error_prefix_with_label(self) -> None:
        """##[error] with extra whitespace is stripped cleanly."""
        from ci_context.analysis.normalizer import normalize_to_text

        raw = "##[error]   Unexpected lint error found"
        result = normalize_to_text(raw)
        self.assertEqual(result, "Unexpected lint error found")


if __name__ == "__main__":
    unittest.main()

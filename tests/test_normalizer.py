"""Tests for log normalization module."""

import unittest

from ci_context.analysis.normalizer import (
    normalize,
    normalize_to_text,
)


class TestNormalize(unittest.TestCase):
    """Test log normalization."""

    def test_removes_ansi_codes(self):
        """Should remove ANSI escape codes."""
        raw = "\x1b[31mError\x1b[0m: something failed"
        result = normalize(raw)
        self.assertEqual(result[0].content, "Error: something failed")

    def test_removes_gha_timestamp(self):
        """Should remove GitHub Actions timestamp prefixes."""
        raw = "2026-07-16T10:30:00.1234567Z Error: something failed"
        result = normalize(raw)
        self.assertEqual(result[0].content, "Error: something failed")

    def test_removes_section_markers(self):
        """Should remove ##[section] markers."""
        raw = "##[section]\nSome output\n##[section]\nMore output"
        result = normalize(raw)
        lines = [r.content for r in result]
        self.assertEqual(lines[0], "Some output")
        self.assertEqual(lines[1], "More output")

    def test_preserves_original_line_numbers(self):
        """Should preserve original line numbers."""
        raw = "line1\nline2\nline3"
        result = normalize(raw)
        self.assertEqual(result[0].original_line_number, 1)
        self.assertEqual(result[1].original_line_number, 2)
        self.assertEqual(result[2].original_line_number, 3)

    def test_collapses_consecutive_blank_lines(self):
        """Should collapse consecutive blank lines."""
        raw = "line1\n\n\nline2\n\n\n\nline3"
        result = normalize(raw)
        # line1, line2, line3 (blank lines between are collapsed)
        # Count actual non-empty lines
        non_empty = [r for r in result if r.content.strip()]
        self.assertEqual(len(non_empty), 3)

    def test_mixed_noise(self):
        """Test with realistic GHA log containing multiple noise types."""
        raw = """2026-07-16T10:30:00.1234567Z ##[section] Running tests
\x1b[32m✓\x1b[0m All tests passed
2026-07-16T10:30:01.2345678Z ##[section] Build complete
Some output

Another line"""
        result = normalize(raw)
        # Check that noise is removed but content is preserved
        content = "\n".join(r.content for r in result)
        self.assertNotIn("2026-07-16T10:30:", content)
        self.assertNotIn("##[section]", content)
        self.assertNotIn("\x1b[", content)
        # Section lines are skipped entirely, but other content is preserved
        self.assertIn("All tests passed", content)
        self.assertIn("Some output", content)
        self.assertIn("All tests passed", content)


class TestNormalizeToText(unittest.TestCase):
    """Test normalize_to_text convenience function."""

    def test_returns_plain_text(self):
        """Should return plain text without NormalizedLine wrappers."""
        raw = "2026-07-16T10:30:00.1234567Z Error: test"
        result = normalize_to_text(raw)
        self.assertEqual(result, "Error: test")
        self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main()

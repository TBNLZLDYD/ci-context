"""Tests for error fingerprint computation."""

from __future__ import annotations

import unittest

from ci_context.analysis.fingerprint import (
    _normalize_message,
    _replace_numbers,
    _replace_paths,
    _replace_shas,
    compute_fingerprint,
)
from ci_context.models.error import ExtractedError


class TestReplaceShas(unittest.TestCase):
    """SHA normalization: 7-40 hex chars replaced, 0x-prefixed hex left alone."""

    def test_seven_char_sha_replaced(self) -> None:
        self.assertEqual(_replace_shas("commit abc123d"), "commit <SHA>")

    def test_forty_char_sha_replaced(self) -> None:
        sha = "a" * 40
        self.assertEqual(_replace_shas(f"at {sha}"), "at <SHA>")

    def test_six_hex_chars_not_replaced(self) -> None:
        # Below the 7-char threshold — not a SHA
        self.assertEqual(_replace_shas("code abc123"), "code abc123")

    def test_0x_prefix_not_replaced(self) -> None:
        # Hex literal like 0x3a must survive
        self.assertEqual(_replace_shas("offset 0x3a"), "offset 0x3a")

    def test_all_hex_identifier_replaced(self) -> None:
        # "abc123def" is 9 hex chars — indistinguishable from a short SHA.
        # In real CI logs, standalone 7+ hex strings are almost always SHAs.
        self.assertEqual(_replace_shas("var abc123def"), "var <SHA>")

    def test_mixed_alphanumeric_not_replaced(self) -> None:
        # Contains non-hex chars (g, h, i, j, k, l, m, n, o, p, q, r, s, t,
        # u, v, w, x, y, z beyond a-f) so it can't be a SHA.
        self.assertEqual(_replace_shas("var abc123xyz"), "var abc123xyz")

    def test_multiple_shas_replaced(self) -> None:
        self.assertEqual(
            _replace_shas("from abc1234 to def5678901"),
            "from <SHA> to <SHA>",
        )


class TestReplacePaths(unittest.TestCase):
    """Path normalization: directory portion replaced with <ROOT>/."""

    def test_posix_absolute_path(self) -> None:
        self.assertEqual(
            _replace_paths("error at /home/user/project/src/main.py:42"),
            "error at <ROOT>/main.py:42",
        )

    def test_posix_absolute_path_with_line_col(self) -> None:
        self.assertEqual(
            _replace_paths("at /usr/src/app/utils.py:10:25"),
            "at <ROOT>/utils.py:10:25",
        )

    def test_windows_absolute_path(self) -> None:
        self.assertEqual(
            _replace_paths(r"error in C:\Users\foo\bar.py"),
            "error in <ROOT>/bar.py",
        )

    def test_relative_dot_slash_path(self) -> None:
        self.assertEqual(
            _replace_paths("./main.go:10:2: syntax error"),
            "<ROOT>/main.go:10:2: syntax error",
        )

    def test_multi_segment_relative_path(self) -> None:
        self.assertEqual(
            _replace_paths("src/main.py:42"),
            "<ROOT>/main.py:42",
        )

    def test_bare_filename_not_replaced(self) -> None:
        # Single-segment name is ambiguous — left as-is
        self.assertEqual(_replace_paths("main.py"), "main.py")

    def test_path_with_multiple_segments(self) -> None:
        self.assertEqual(
            _replace_paths("/a/b/c/d/e/file.go:1:1"),
            "<ROOT>/file.go:1:1",
        )


class TestReplaceNumbers(unittest.TestCase):
    """Number normalization: standalone decimals replaced with <NUM>."""

    def test_standalone_number(self) -> None:
        self.assertEqual(_replace_numbers("line 42"), "line <NUM>")

    def test_error_code(self) -> None:
        self.assertEqual(_replace_numbers("error code 127"), "error code <NUM>")

    def test_number_inside_word_not_replaced(self) -> None:
        # "utf8" or "python3" — number is part of an identifier
        self.assertEqual(_replace_numbers("utf8 encoding"), "utf8 encoding")

    def test_0x_prefix_number_not_replaced(self) -> None:
        self.assertEqual(_replace_numbers("0x3a"), "0x3a")

    def test_line_col_numbers(self) -> None:
        self.assertEqual(
            _replace_numbers("main.py:42:3"),
            "main.py:<NUM>:<NUM>",
        )

    def test_multiple_numbers(self) -> None:
        self.assertEqual(
            _replace_numbers("lines 10 to 20"),
            "lines <NUM> to <NUM>",
        )


class TestNormalizeMessage(unittest.TestCase):
    """Full normalization pipeline: SHAs -> paths -> numbers -> lowercase."""

    def test_sha_then_numbers(self) -> None:
        # SHA replaced first, so its hex digits don't become <NUM>
        result = _normalize_message("commit abc123def failed at line 42")
        self.assertEqual(result, "commit <sha> failed at line <num>")

    def test_path_then_numbers(self) -> None:
        # Path replaced first, then numbers in :line:col suffix
        result = _normalize_message("/home/user/src/main.py:42:3 error")
        self.assertEqual(result, "<root>/main.py:<num>:<num> error")

    def test_full_pipeline(self) -> None:
        result = _normalize_message(
            "Python Traceback: /home/user/project/app.py:99 ValueError: "
            "invalid literal for int() with base 10 at abc1234"
        )
        self.assertIn("<root>/app.py:<num>", result)
        self.assertIn("<sha>", result)
        self.assertIn("<num>", result)
        # Entire result is lowercase
        self.assertEqual(result, result.lower())


class TestComputeFingerprint(unittest.TestCase):
    """Fingerprint stability and differentiation."""

    def test_same_error_same_fingerprint(self) -> None:
        err1 = ExtractedError(
            error_type="Python Traceback",
            message="ValueError: invalid literal for int() with base 10",
        )
        err2 = ExtractedError(
            error_type="Python Traceback",
            message="ValueError: invalid literal for int() with base 10",
        )
        self.assertEqual(compute_fingerprint(err1), compute_fingerprint(err2))

    def test_different_line_same_fingerprint(self) -> None:
        # Line numbers are normalized away
        err1 = ExtractedError(
            error_type="Python Traceback",
            message="Error at /home/user/src/app.py:10",
        )
        err2 = ExtractedError(
            error_type="Python Traceback",
            message="Error at /home/user/src/app.py:99",
        )
        self.assertEqual(compute_fingerprint(err1), compute_fingerprint(err2))

    def test_different_path_same_fingerprint(self) -> None:
        # Different project roots normalize to <ROOT>/
        err1 = ExtractedError(
            error_type="Go build error",
            message="/home/alice/project/main.go:10:2: syntax error",
        )
        err2 = ExtractedError(
            error_type="Go build error",
            message="/home/bob/workspace/main.go:10:2: syntax error",
        )
        self.assertEqual(compute_fingerprint(err1), compute_fingerprint(err2))

    def test_different_sha_same_fingerprint(self) -> None:
        # Commit SHAs are normalized away
        err1 = ExtractedError(
            error_type="Shell exit code",
            message="Error: process abc1234 exited with code 1",
        )
        err2 = ExtractedError(
            error_type="Shell exit code",
            message="Error: process def5678 exited with code 1",
        )
        self.assertEqual(compute_fingerprint(err1), compute_fingerprint(err2))

    def test_different_error_type_different_fingerprint(self) -> None:
        err1 = ExtractedError(error_type="Python Traceback", message="foo")
        err2 = ExtractedError(error_type="Go panic", message="foo")
        self.assertNotEqual(compute_fingerprint(err1), compute_fingerprint(err2))

    def test_different_message_different_fingerprint(self) -> None:
        err1 = ExtractedError(error_type="Python Traceback", message="foo")
        err2 = ExtractedError(error_type="Python Traceback", message="bar")
        self.assertNotEqual(compute_fingerprint(err1), compute_fingerprint(err2))

    def test_fingerprint_is_16_hex_chars(self) -> None:
        err = ExtractedError(error_type="Test", message="test message")
        fp = compute_fingerprint(err)
        self.assertEqual(len(fp), 16)
        # All hex characters
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))

    def test_windows_path_normalization(self) -> None:
        err1 = ExtractedError(
            error_type="Error + stack (Node.js)",
            message=r"Cannot find module at C:\Users\foo\project\index.js:5",
        )
        err2 = ExtractedError(
            error_type="Error + stack (Node.js)",
            message=r"Cannot find module at C:\Users\bar\workspace\index.js:5",
        )
        self.assertEqual(compute_fingerprint(err1), compute_fingerprint(err2))

    def test_relative_path_normalization(self) -> None:
        err1 = ExtractedError(
            error_type="Go build error",
            message="./cmd/main.go:10:2: undefined: foo",
        )
        err2 = ExtractedError(
            error_type="Go build error",
            message="./cmd/main.go:20:5: undefined: foo",
        )
        # Line numbers differ but message is otherwise the same
        self.assertEqual(compute_fingerprint(err1), compute_fingerprint(err2))


if __name__ == "__main__":
    unittest.main()

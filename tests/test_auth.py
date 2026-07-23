"""Tests for GitHub authentication module."""

import os
import unittest
import warnings
from unittest.mock import MagicMock, patch

from ci_context.github.auth import (
    _check_config_permissions,
    _gh_available,
    _read_config_token,
    resolve_token,
)
from ci_context.github.exceptions import AuthError


class TestResolveToken(unittest.TestCase):
    """Test token resolution priority."""

    @patch.dict(os.environ, {}, clear=True)
    @patch("ci_context.github.auth._read_config_token", return_value=None)
    @patch("ci_context.github.auth._gh_available", return_value=False)
    def test_no_token_raises_auth_error(self, mock_gh, mock_config):
        """Should raise AuthError when no token is available."""
        with self.assertRaises(AuthError) as ctx:
            resolve_token(None)
        # Check that tried methods are recorded
        self.assertIn("CLI --token", ctx.exception.tried)
        self.assertIn("config file", ctx.exception.tried)

    def test_cli_token_takes_priority(self):
        """CLI token should be returned directly."""
        result = resolve_token("cli-token-123")
        self.assertEqual(result, "cli-token-123")

    @patch.dict(os.environ, {}, clear=True)
    @patch("ci_context.github.auth._read_config_token", return_value="config-token-456")
    def test_config_token_used_when_no_cli_token(self, mock_config):
        """Config token should be used when CLI token is not provided."""
        result = resolve_token(None)
        self.assertEqual(result, "config-token-456")
        mock_config.assert_called_once()

    @patch("subprocess.run")
    @patch("ci_context.github.auth._read_config_token", return_value=None)
    @patch("ci_context.github.auth._gh_available", return_value=True)
    def test_gh_auth_token_as_fallback(self, mock_gh, mock_config, mock_subprocess):
        """gh auth token should be used as fallback."""
        mock_result = MagicMock()
        mock_result.stdout.strip.return_value = "gh-token-789"
        mock_subprocess.return_value = mock_result

        result = resolve_token(None)
        self.assertEqual(result, "gh-token-789")


class TestGhAvailable(unittest.TestCase):
    """Test gh CLI availability check."""

    @patch("shutil.which", return_value=None)
    def test_gh_not_available(self, mock_which):
        """Should return False when gh is not installed."""
        result = _gh_available()
        self.assertFalse(result)
        mock_which.assert_called_once_with("gh")

    @patch("shutil.which", return_value="/path/to/gh")
    def test_gh_available(self, mock_which):
        """Should return True when gh is installed."""
        result = _gh_available()
        self.assertTrue(result)


class TestReadConfigToken(unittest.TestCase):
    """Test config file token reading."""

    @patch("ci_context.github.auth.Path.exists", return_value=False)
    def test_no_config_file_returns_none(self, mock_exists):
        """Should return None when config file does not exist."""
        result = _read_config_token()
        self.assertIsNone(result)


class TestCheckConfigPermissions(unittest.TestCase):
    """Test config file permission checking."""

    @patch("os.name", "nt")
    def test_skips_on_windows(self):
        """Should skip permission check on Windows."""
        # Use MagicMock to avoid PosixPath/WindowsPath cross-platform instantiation issues
        mock_path = MagicMock()
        _check_config_permissions(mock_path)  # No exception = pass
        # The function should return immediately without calling stat() on Windows
        mock_path.stat.assert_not_called()

    @patch("os.name", "posix")
    def test_no_warn_on_restricted_file(self):
        """Should not warn when config file is owner-only (0600)."""
        import stat

        mock_path = MagicMock()
        mock_path.stat().st_mode = 0o600 | stat.S_IFREG
        # Should not emit UserWarning
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _check_config_permissions(mock_path)
            user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
            self.assertEqual(len(user_warnings), 0)

    @patch("os.name", "posix")
    def test_warns_on_world_readable_file(self):
        """Should warn when config file is world-readable (0644)."""
        import stat

        mock_path = MagicMock()
        mock_path.stat().st_mode = 0o644 | stat.S_IFREG
        # The warning message includes the mock's string repr; just verify it fires
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _check_config_permissions(mock_path)
            user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
            self.assertEqual(len(user_warnings), 1)
            self.assertIn("world-readable", str(user_warnings[0].message))
            self.assertIn("chmod 600", str(user_warnings[0].message))

    def test_stat_failure_silently_ignored(self):
        """Should not raise when stat() fails (e.g., file deleted between checks)."""
        # Use a MagicMock instead of a real Path to avoid PosixPath/WindowsPath issues
        mock_path = MagicMock()
        mock_path.stat.side_effect = OSError("No such file")
        with patch("os.name", "posix"):
            _check_config_permissions(mock_path)  # No exception = pass


if __name__ == "__main__":
    unittest.main()

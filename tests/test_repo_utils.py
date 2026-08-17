"""Tests for repository resolution utilities (cli/repo_utils.py)."""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

import ci_context.cli.repo_utils as repo_utils


class TestResolveRepo(unittest.TestCase):
    """Test resolve_repo argument handling and git-remote inference."""

    def test_valid_arg_returned_as_is(self):
        """A well-formed 'owner/repo' argument must be returned unchanged."""
        self.assertEqual(repo_utils.resolve_repo("owner/repo"), "owner/repo")

    def test_invalid_arg_raises_value_error(self):
        """A malformed argument must raise ValueError with a descriptive message."""
        with self.assertRaises(ValueError) as ctx:
            repo_utils.resolve_repo("invalid")
        self.assertIn("Invalid repository format", str(ctx.exception))

    def test_none_arg_uses_https_remote(self):
        """With no argument, an HTTPS origin must be parsed to owner/repo."""
        with patch(
            "ci_context.cli.repo_utils._get_git_remote_origin",
            return_value="https://github.com/owner/repo.git",
        ):
            self.assertEqual(repo_utils.resolve_repo(None), "owner/repo")

    def test_none_arg_uses_ssh_remote(self):
        """With no argument, an SSH origin must be parsed to owner/repo."""
        with patch(
            "ci_context.cli.repo_utils._get_git_remote_origin",
            return_value="git@github.com:owner/repo.git",
        ):
            self.assertEqual(repo_utils.resolve_repo(None), "owner/repo")

    def test_none_arg_without_remote_raises(self):
        """With no argument and no resolvable remote, a helpful ValueError must raise."""
        with (
            patch("ci_context.cli.repo_utils._get_git_remote_origin", return_value=None),
            self.assertRaises(ValueError) as ctx,
        ):
            repo_utils.resolve_repo(None)
        self.assertIn("Cannot determine repository", str(ctx.exception))

    def test_none_arg_with_unparseable_remote_raises(self):
        """A non-GitHub remote that parses to nothing must raise, not silently return."""
        with (
            patch(
                "ci_context.cli.repo_utils._get_git_remote_origin",
                return_value="https://gitlab.com/foo/bar.git",
            ),
            self.assertRaises(ValueError) as ctx,
        ):
            repo_utils.resolve_repo(None)
        self.assertIn("Cannot determine repository", str(ctx.exception))


class TestGetGitRemoteOrigin(unittest.TestCase):
    """Test the git subprocess wrapper."""

    def test_returns_stripped_stdout(self):
        """The raw git output must be stripped of surrounding whitespace."""
        mock_result = MagicMock()
        mock_result.stdout = "  https://github.com/owner/repo.git  \n"
        with patch("ci_context.cli.repo_utils.subprocess.run", return_value=mock_result) as m:
            result = repo_utils._get_git_remote_origin()
        self.assertEqual(result, "https://github.com/owner/repo.git")
        # The subprocess must be invoked with the exact git get-url command shape.
        m.assert_called_once_with(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )

    def test_subprocess_error_returns_none(self):
        """A failing subprocess (e.g. not a git repo) must yield None, not raise."""
        with patch(
            "ci_context.cli.repo_utils.subprocess.run",
            side_effect=subprocess.SubprocessError("boom"),
        ):
            self.assertIsNone(repo_utils._get_git_remote_origin())

    def test_file_not_found_returns_none(self):
        """A missing git executable must yield None, not raise."""
        with patch(
            "ci_context.cli.repo_utils.subprocess.run",
            side_effect=FileNotFoundError("git"),
        ):
            self.assertIsNone(repo_utils._get_git_remote_origin())


class TestParseGitRemoteUrl(unittest.TestCase):
    """Test URL parsing for HTTPS and SSH formats."""

    def test_https_with_dot_git(self):
        """https://github.com/owner/repo.git must parse to owner/repo."""
        self.assertEqual(
            repo_utils._parse_git_remote_url("https://github.com/owner/repo.git"),
            "owner/repo",
        )

    def test_https_without_dot_git(self):
        """https://github.com/owner/repo must parse to owner/repo."""
        self.assertEqual(
            repo_utils._parse_git_remote_url("https://github.com/owner/repo"),
            "owner/repo",
        )

    def test_ssh_with_dot_git(self):
        """git@github.com:owner/repo.git must parse to owner/repo."""
        self.assertEqual(
            repo_utils._parse_git_remote_url("git@github.com:owner/repo.git"),
            "owner/repo",
        )

    def test_ssh_without_dot_git(self):
        """git@github.com:owner/repo must parse to owner/repo."""
        self.assertEqual(
            repo_utils._parse_git_remote_url("git@github.com:owner/repo"),
            "owner/repo",
        )

    def test_subpath_returns_none(self):
        """A URL with an extra path segment must be rejected (only owner/repo)."""
        self.assertIsNone(
            repo_utils._parse_git_remote_url("https://github.com/owner/repo/sub/path")
        )

    def test_trailing_slash_returns_none(self):
        """A trailing slash after the repo name must be rejected."""
        self.assertIsNone(repo_utils._parse_git_remote_url("https://github.com/owner/repo/"))

    def test_wrong_host_returns_none(self):
        """A non-GitHub host must not parse to a GitHub owner/repo."""
        self.assertIsNone(repo_utils._parse_git_remote_url("https://gitlab.com/owner/repo.git"))

    def test_empty_url_returns_none(self):
        """An empty URL must yield None rather than crashing."""
        self.assertIsNone(repo_utils._parse_git_remote_url(""))


class TestValidateRepoFormat(unittest.TestCase):
    """Test the owner/repo format validator."""

    def test_valid_format(self):
        """Exactly one slash separating owner and repo must validate."""
        self.assertTrue(repo_utils._validate_repo_format("a/b"))

    def test_no_slash_invalid(self):
        """A single segment without a slash must be rejected."""
        self.assertFalse(repo_utils._validate_repo_format("a"))

    def test_extra_slash_invalid(self):
        """More than one slash must be rejected."""
        self.assertFalse(repo_utils._validate_repo_format("a/b/c"))

    def test_empty_invalid(self):
        """An empty string must be rejected."""
        self.assertFalse(repo_utils._validate_repo_format(""))

    def test_leading_or_trailing_slash_invalid(self):
        """A leading or trailing slash must be rejected (no empty segments)."""
        self.assertFalse(repo_utils._validate_repo_format("/b"))
        self.assertFalse(repo_utils._validate_repo_format("a/"))


if __name__ == "__main__":
    unittest.main()

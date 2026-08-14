"""Tests for commit data fetching (get_commit_context)."""

import unittest
from unittest.mock import MagicMock

from ci_context.github.commits import MAX_CHANGED_FILES, get_commit_context
from ci_context.models.commit import CommitInfo


class TestGetCommitContext(unittest.TestCase):
    """Test get_commit_context for successful fetches and graceful degradation."""

    def _make_client(self) -> MagicMock:
        """Create a mock GitHubClient."""
        return MagicMock()

    def _make_file(self, path: str, additions: int, deletions: int) -> MagicMock:
        """Build a single changed-file object as PyGithub returns."""
        f = MagicMock()
        f.filename = path
        f.additions = additions
        f.deletions = deletions
        return f

    def _make_commit(self, files: list) -> MagicMock:
        """Build a commit whose .files is a real list, as PyGithub returns."""
        commit = MagicMock()
        commit.sha = "abc123"
        commit.commit.message = "fix the build"
        commit.commit.author.name = "alice"
        commit.files = files
        return commit

    def test_successful_fetch(self):
        """Should return CommitInfo with sha, message, author, and all changed files."""
        client = self._make_client()
        files = [
            self._make_file("a.py", 10, 2),
            self._make_file("b.py", 0, 5),
            self._make_file("c.py", 3, 0),
        ]
        client.get_repo.return_value.get_commit.return_value = self._make_commit(files)

        result = get_commit_context(client, "owner/repo", "abc123")

        self.assertIsInstance(result, CommitInfo)
        self.assertEqual(result.sha, "abc123")
        self.assertEqual(result.message, "fix the build")
        self.assertEqual(result.author, "alice")
        self.assertEqual(len(result.changed_files), 3)
        self.assertEqual(result.changed_files[0].path, "a.py")
        self.assertEqual(result.changed_files[0].additions, 10)
        self.assertEqual(result.changed_files[0].deletions, 2)
        self.assertEqual(result.changed_files[1].path, "b.py")
        self.assertEqual(result.changed_files[1].additions, 0)
        self.assertEqual(result.changed_files[1].deletions, 5)
        self.assertEqual(result.changed_files[2].path, "c.py")
        self.assertEqual(result.changed_files[2].additions, 3)
        self.assertEqual(result.changed_files[2].deletions, 0)

    def test_none_message_and_author_degrades(self):
        """Should degrade None message/author to empty strings instead of crashing."""
        client = self._make_client()
        commit = self._make_commit([])
        commit.commit.message = None
        commit.commit.author.name = None
        client.get_repo.return_value.get_commit.return_value = commit

        result = get_commit_context(client, "owner/repo", "abc123")

        self.assertEqual(result.message, "")
        self.assertEqual(result.author, "")

    def test_more_than_50_changed_files_capped(self):
        """Should cap changed_files at MAX_CHANGED_FILES (50) for huge commits."""
        client = self._make_client()
        files = [self._make_file(f"f{i}.py", 1, 1) for i in range(60)]
        client.get_repo.return_value.get_commit.return_value = self._make_commit(files)

        result = get_commit_context(client, "owner/repo", "abc123")

        self.assertEqual(len(result.changed_files), MAX_CHANGED_FILES)
        self.assertEqual(result.changed_files[0].path, "f0.py")

    def test_fetch_failure_returns_stub(self):
        """Should return stub CommitInfo and not raise when get_commit fails."""
        client = self._make_client()
        client.get_repo.return_value.get_commit.side_effect = RuntimeError("boom")

        result = get_commit_context(client, "owner/repo", "deadbeef")

        self.assertEqual(result, CommitInfo(sha="deadbeef", message="", author=""))

    def test_get_repo_failure_propagates(self):
        """Should raise when client.get_repo fails - repo-level errors surface, not stubbed."""
        client = self._make_client()
        client.get_repo.side_effect = RuntimeError("repo fetch failed")

        with self.assertRaises(RuntimeError):
            get_commit_context(client, "owner/repo", "abc123")

    def test_falsy_commit_sha_falls_back_to_requested(self):
        """Should fall back to the requested sha when commit.sha is falsy."""
        client = self._make_client()
        commit = self._make_commit([])
        commit.sha = ""
        client.get_repo.return_value.get_commit.return_value = commit

        result = get_commit_context(client, "owner/repo", "abc123")

        self.assertEqual(result.sha, "abc123")


if __name__ == "__main__":
    unittest.main()

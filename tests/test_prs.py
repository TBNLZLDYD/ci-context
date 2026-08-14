"""Tests for PR data fetching (get_pr_context)."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock

from ci_context.github.prs import (
    MAX_PR_BODY_CHARS,
    MAX_REVIEW_BODY_CHARS,
    MAX_REVIEWS,
    get_pr_context,
)
from ci_context.models.pr import PRInfo


class _FakeReviewList:
    """PyGithub PaginatedList stand-in whose .reversed yields newest-first."""

    def __init__(self, items):
        self._items = items

    @property
    def reversed(self):
        return self._items


class TestGetPRContext(unittest.TestCase):
    """Test get_pr_context for successful fetches and graceful degradation."""

    def _make_client(self) -> MagicMock:
        """Create a mock GitHubClient."""
        return MagicMock()

    def _make_review(
        self,
        state: str,
        login: str | None,
        body: str | None,
        submitted_at: datetime | None,
    ) -> MagicMock:
        """Build a review; login=None simulates a deleted-account review."""
        review = MagicMock()
        review.state = state
        review.user = None if login is None else MagicMock()
        if login is not None:
            review.user.login = login
        review.body = body
        review.submitted_at = submitted_at
        return review

    def _make_pr(
        self,
        reviews: list,
        *,
        number: int = 42,
        title: str = "Fix the build",
        author: str = "alice",
        state: str = "open",
        merged: bool = False,
        body: str | None = "Summary of the change",
    ) -> MagicMock:
        """Build a PR whose get_reviews returns a real newest-first list."""
        pr = MagicMock()
        pr.number = number
        pr.title = title
        pr.user = MagicMock()
        pr.user.login = author
        pr.state = state
        pr.merged = merged
        pr.body = body
        pr.get_reviews.return_value = _FakeReviewList(reviews)
        return pr

    def test_open_pr_with_decisive_review(self):
        """Should report status=open and take the newest decisive review state."""
        client = self._make_client()
        reviews = [
            self._make_review(
                "APPROVED", "bob", "Looks good", datetime(2026, 8, 1, 12, 0)
            ),
            # COMMENTED carries no verdict, so it must not override the approval
            self._make_review(
                "COMMENTED", "carol", "Nit", datetime(2026, 7, 30, 9, 0)
            ),
        ]
        pr = self._make_pr(reviews)
        client.get_repo.return_value.get_pull.return_value = pr

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(result.number, 42)
        self.assertEqual(result.status, "open")
        self.assertEqual(result.review_state, "approved")
        self.assertEqual(len(result.latest_reviews), 2)
        self.assertEqual(result.latest_reviews[0].author, "bob")
        self.assertEqual(result.latest_reviews[0].body, "Looks good")
        self.assertNotEqual(result.body_snippet, "")

    def test_merged_pr(self):
        """Should report status=merged when pr.merged is True (state is closed)."""
        client = self._make_client()
        pr = self._make_pr([], state="closed", merged=True)
        client.get_repo.return_value.get_pull.return_value = pr

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(result.status, "merged")

    def test_closed_without_merge(self):
        """Should report status=closed when state=closed but pr.merged is False."""
        client = self._make_client()
        pr = self._make_pr([], state="closed", merged=False)
        client.get_repo.return_value.get_pull.return_value = pr

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(result.status, "closed")

    def test_no_decisive_review_means_pending(self):
        """Should report review_state=pending when only non-decisive reviews exist."""
        client = self._make_client()
        reviews = [
            self._make_review("COMMENTED", "bob", "LGTM?", None),
            self._make_review("PENDING", "carol", None, None),
        ]
        pr = self._make_pr(reviews)
        client.get_repo.return_value.get_pull.return_value = pr

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(result.review_state, "pending")

    def test_truncates_pr_body_and_review_body(self):
        """Should cap PR body at 500 chars and review body at 200 chars."""
        client = self._make_client()
        review = self._make_review(
            "APPROVED", "bob", "y" * 300, datetime(2026, 8, 1, 12, 0)
        )
        pr = self._make_pr([review], body="x" * 600)
        client.get_repo.return_value.get_pull.return_value = pr

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(len(result.body_snippet), MAX_PR_BODY_CHARS)
        self.assertEqual(len(result.latest_reviews[0].body), MAX_REVIEW_BODY_CHARS)

    def test_more_than_five_reviews_capped(self):
        """Should cap latest_reviews at MAX_REVIEWS (5) for review-heavy PRs."""
        client = self._make_client()
        reviews = [
            self._make_review("COMMENTED", f"user{i}", None, datetime(2026, 8, 1))
            for i in range(6)
        ]
        pr = self._make_pr(reviews)
        client.get_repo.return_value.get_pull.return_value = pr

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(len(result.latest_reviews), MAX_REVIEWS)

    def test_null_authors_and_timestamps_degrades(self):
        """Should degrade None PR user, review user, and submitted_at to empty strings."""
        client = self._make_client()
        review = self._make_review("APPROVED", None, "body", None)
        pr = self._make_pr([review])
        pr.user = None  # deleted account — the PR itself has no author
        client.get_repo.return_value.get_pull.return_value = pr

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(result.author, "")
        self.assertEqual(result.latest_reviews[0].author, "")
        self.assertEqual(result.latest_reviews[0].created_at, "")

    def test_get_repo_failure_propagates(self):
        """Should raise when client.get_repo fails - repo-level errors surface, not stubbed."""
        client = self._make_client()
        client.get_repo.side_effect = RuntimeError("repo fetch failed")

        with self.assertRaises(RuntimeError):
            get_pr_context(client, "owner/repo", 42)

    def test_fetch_failure_returns_stub(self):
        """Should return stub PRInfo and not raise when get_pull fails."""
        client = self._make_client()
        client.get_repo.return_value.get_pull.side_effect = RuntimeError("boom")

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(
            result,
            PRInfo(number=42, title="", author="", status="unknown", review_state="pending"),
        )

    def test_pr_body_none_yields_empty_snippet(self):
        """Should yield an empty body_snippet when the PR body is None."""
        client = self._make_client()
        pr = self._make_pr([], body=None)
        client.get_repo.return_value.get_pull.return_value = pr

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(result.body_snippet, "")

    def test_dismissed_review_is_ignored(self):
        """Should skip a newest DISMISSED review and use the older decisive one."""
        client = self._make_client()
        reviews = [
            self._make_review("DISMISSED", "bob", None, datetime(2026, 8, 3, 9, 0)),
            self._make_review(
                "APPROVED", "alice", "Looks good", datetime(2026, 8, 1, 12, 0)
            ),
        ]
        pr = self._make_pr(reviews)
        client.get_repo.return_value.get_pull.return_value = pr

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(result.review_state, "approved")

    def test_changes_requested_state(self):
        """Should report changes_requested from a CHANGES_REQUESTED review."""
        client = self._make_client()
        reviews = [
            self._make_review(
                "CHANGES_REQUESTED", "bob", "Fix tests", datetime(2026, 8, 5, 9, 0)
            ),
        ]
        pr = self._make_pr(reviews)
        client.get_repo.return_value.get_pull.return_value = pr

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(result.review_state, "changes_requested")

    def test_newest_decisive_wins_over_earlier_non_decisive(self):
        """Should pick the newest decisive review, skipping non-decisive newer ones."""
        client = self._make_client()
        reviews = [
            self._make_review("COMMENTED", "carol", "Nit", datetime(2026, 8, 4, 9, 0)),
            self._make_review(
                "APPROVED", "bob", "Looks good", datetime(2026, 8, 3, 12, 0)
            ),
            self._make_review(
                "CHANGES_REQUESTED", "alice", "Needs work", datetime(2026, 8, 1, 9, 0)
            ),
        ]
        pr = self._make_pr(reviews)
        client.get_repo.return_value.get_pull.return_value = pr

        result = get_pr_context(client, "owner/repo", 42)

        self.assertEqual(result.review_state, "approved")


if __name__ == "__main__":
    unittest.main()

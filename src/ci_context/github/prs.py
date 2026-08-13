"""PR data fetching — get PR details, reviews, and review comments.

For CI failure diagnosis the PR context tells whether the change was accepted
(review_state) and what reviewers said most recently (latest_reviews) — that
separates a build break on an in-flight change from one on an approved merge.
This module maps PyGithub's PullRequest / PullRequestReview objects onto the
PRInfo / ReviewComment models; every fetch failure degrades to a stub so a
missing or unviewable PR never aborts the diagnosis.
"""

from __future__ import annotations

import logging
from itertools import islice

from github.PullRequest import PullRequest as PyGithubPullRequest
from github.PullRequestReview import PullRequestReview as PyGithubPullRequestReview

from ci_context.github.client import GitHubClient
from ci_context.models.pr import PRInfo, ReviewComment

logger = logging.getLogger(__name__)

# Reviewers rarely need more than a handful of recent reviews to get the gist,
# and long-lived PRs can accumulate many — cap keeps the report compact.
MAX_REVIEWS = 5

# Body caps keep the report readable; the full text always lives in the GitHub
# UI, so the data layer only needs enough to summarize.
MAX_REVIEW_BODY_CHARS = 200
MAX_PR_BODY_CHARS = 500

# Only review states that represent an actual decision count toward
# review_state. COMMENTED is just noise, while PENDING and DISMISSED carry no
# current verdict (a dismissed approval is no longer an approval).
_DECISION_REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED"}


def get_pr_context(client: GitHubClient, owner_repo: str, pr_number: int) -> PRInfo:
    """
    Get a pull request's context (title, author, status, reviews, body).

    Args:
        client: GitHubClient instance
        owner_repo: Repository in "owner/repo" format
        pr_number: Pull request number

    Returns:
        PRInfo dataclass

    Never raises; a failed fetch degrades to a stub PRInfo that keeps the
    requested number so the report can still reference the PR.
    """
    repo = client.get_repo(owner_repo)
    try:
        pr: PyGithubPullRequest = repo.get_pull(pr_number)
        reviews = _fetch_reviews(pr)
        return PRInfo(
            number=pr.number,
            title=pr.title or "",
            # user can be null for deleted accounts — degrade to empty rather
            # than crashing the whole report over one missing author
            author=pr.user.login if pr.user else "",
            status=_derive_status(pr),
            review_state=_derive_review_state(reviews),
            latest_reviews=_build_review_comments(reviews),
            body_snippet=_truncate(pr.body, MAX_PR_BODY_CHARS),
        )
    except Exception as e:
        # A failed fetch (404 unknown PR, rate limit, network) must not abort
        # the whole diagnosis — return a stub keeping the number so the report
        # can still reference which PR we tried to inspect.
        logger.warning("Could not fetch PR %d in %s: %s", pr_number, owner_repo, e)
        return PRInfo(
            number=pr_number,
            title="",
            author="",
            status="unknown",
            review_state="pending",
        )


def _fetch_reviews(pr: PyGithubPullRequest) -> list[PyGithubPullRequestReview]:
    """Return the PR's reviews newest-first, capped at MAX_REVIEWS.

    The reviews endpoint lists oldest-first, so we iterate the PaginatedList in
    reverse and stop after the cap — this avoids walking every page on PRs with
    many reviews, a pathological case whose only cost here is wasted requests.
    """
    # `.reversed` (not built-in reversed()) because PyGithub's PaginatedList
    # exposes an explicit reverse-iteration property typed for mypy.
    newest_first = pr.get_reviews().reversed
    return list(islice(newest_first, MAX_REVIEWS))


def _derive_status(pr: PyGithubPullRequest) -> str:
    """Map PyGithub's state/merged fields onto our status vocabulary.

    A merged PR reports state='closed' with merged=True; checking merged first
    distinguishes it from a closed-without-merge PR.
    """
    if pr.merged:
        return "merged"
    return pr.state or "unknown"


def _derive_review_state(reviews: list[PyGithubPullRequestReview]) -> str:
    """Derive the overall review state from the newest decisive review.

    Reviews arrive newest-first, so the first one with a decision state wins;
    "pending" means no decision is on record yet.
    """
    for review in reviews:
        if (review.state or "") in _DECISION_REVIEW_STATES:
            return review.state.lower()
    return "pending"


def _build_review_comments(reviews: list[PyGithubPullRequestReview]) -> list[ReviewComment]:
    """Map PyGithub reviews onto ReviewComment, truncating bodies.

    A review may lack a user (deleted account) or a submitted timestamp (a
    still-pending review) — those degrade to empty strings rather than crash.
    """
    return [
        ReviewComment(
            author=review.user.login if review.user else "",
            body=_truncate(review.body, MAX_REVIEW_BODY_CHARS),
            created_at=review.submitted_at.isoformat() if review.submitted_at else "",
        )
        for review in reviews
    ]


def _truncate(text: str | None, max_chars: int) -> str:
    """Truncate a string to max_chars; return unchanged if shorter.

    GitHub PR/review bodies can be arbitrarily long, so we cap at the character
    level — word-boundary splitting is a rendering concern, not a data-layer one.
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars]

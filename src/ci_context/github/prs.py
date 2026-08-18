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

import httpx
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


def find_pr_number(client: GitHubClient, owner_repo: str, run_id: int) -> int | None:
    """
    Discover the PR number associated with a workflow run, if any.

    The run REST payload includes a ``pull_requests`` array (usually one entry)
    only when the run was triggered by a pull_request event. PyGithub's
    WorkflowRun object does not expose this field, so we read it via the raw
    httpx client that shares the client's auth.

    Some runs (again pull_request events) come back with an *empty*
    ``pull_requests`` array even though the commit is genuinely attached to a
    PR — in that case we ask the commit's associated-pulls endpoint
    (``GET /repos/{owner}/{repo}/commits/{head_sha}/pulls``) once and take the
    first result.  When the run payload carries no ``head_sha`` there is
    nothing to query, so we give up without a fallback.

    Returns:
        The first associated PR number, or None when absent/empty. Any failure
        (HTTP error, network, malformed payload) degrades to None — PR context
        is optional and must never crash the report.
    """
    owner, repo_name = owner_repo.split("/", 1)
    url = f"/repos/{owner}/{repo_name}/actions/runs/{run_id}"
    try:
        response = client.httpx_client.get(url)
        response.raise_for_status()
        payload = response.json()
        # response.json() is Any under mypy strict — narrow to the known shape.
        if not isinstance(payload, dict):
            return None
        pull_requests = payload.get("pull_requests")
        if isinstance(pull_requests, list) and pull_requests:
            number = _extract_pr_number(pull_requests[0])
            if number is not None:
                return number
        # Empty array (or non-list) fallback: the commit may still belong to a
        # PR. head_sha is only present on pull-request-triggered runs; skip the
        # fallback without it so we never guess a shaft instead of a sha.
        head_sha = payload.get("head_sha")
        if isinstance(head_sha, str) and head_sha:
            pulls_url = f"/repos/{owner}/{repo_name}/commits/{head_sha}/pulls"
            pr_number = _extract_associated_pr(client, pulls_url)
            if pr_number is not None:
                return pr_number
        return None
    except (httpx.HTTPError, httpx.TimeoutException, ValueError, TypeError) as e:
        # ValueError: non-int "number" or malformed JSON; TypeError: unexpected
        # payload nesting. All mean "no PR info we can trust" -> None.
        logger.warning("Could not find PR for run %d in %s: %s", run_id, owner_repo, e)
        return None
    except Exception as e:
        # Catch-all for anything unforeseen so PR discovery stays best-effort.
        logger.warning("Unexpected error finding PR for run %d in %s: %s", run_id, owner_repo, e)
        return None


def _extract_pr_number(item: object) -> int | None:
    """Pull the numeric ``number`` from one JSON array element.

    Shared by the run payload and the commit-association fallback so both go
    through the same shape check (a non-dict element or a non-int number is
    "no PR info", never a crash).
    """
    if not isinstance(item, dict):
        return None
    number = item.get("number")
    return int(number) if isinstance(number, int) else None


def _extract_associated_pr(client: GitHubClient, url: str) -> int | None:
    """Return the first PR number from ``GET url``'s array, or None.

    A non-200 response throws (raised inside the caller's try/except) so the
    caller's degrade-to-None behaviour stays in one place; an empty/absent
    array is just "no association found".
    """
    response = client.httpx_client.get(url)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or not payload:
        return None
    return _extract_pr_number(payload[0])


def get_pr_context(client: GitHubClient, owner_repo: str, pr_number: int) -> PRInfo:
    """
    Get a pull request's context (title, author, status, reviews, body).

    Args:
        client: GitHubClient instance
        owner_repo: Repository in "owner/repo" format
        pr_number: Pull request number

    Returns:
        PRInfo dataclass

    A per-resource fetch failure (the requested PR is unknown, rate-limited, or
    the network fails) is caught and degrades to a stub PRInfo that keeps the
    requested number so the report can still reference the PR. A
    repository-level failure (client.get_repo: bad auth, repo not found) is NOT
    caught here and propagates to the caller — by design, so auth/not-found
    errors surface clearly instead of being silently stubbed.
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

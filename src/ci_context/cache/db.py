"""SQLite cache — store error fingerprints and run metadata to reduce API calls.

Cache layout (three tables, all keyed on the cache row's own created_at):

* ``fingerprints`` — one row per unique 16-char fingerprint.  Carries the
  normalised message so we can reconstruct a synthetic ExtractedError without
  re-running the extractor (useful for future "list known failures" UX).
* ``fingerprint_occurrences`` — where each fingerprint was seen.  This is what
  the history matcher reads; ``UNIQUE(fingerprint, run_id, repo)`` makes one
  row = one (run, fp) pair even when the extractor re-observes the same error
  in the same run.
* ``run_metadata`` — serialised WorkflowRunInfo keyed by (run_id, repo) so the
  next invocation can short-circuit ``get_run``.

TTL strategy: *lazy* expiry.  Every read filters by
``created_at >= datetime('now', '-7 days')`` so an expired row is invisible
to consumers but not deleted.  The CLI is short-lived and single-process —
a background sweeper would add complexity for no real benefit.  Users who
want to reclaim disk space can call :func:`purge_expired` explicitly.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ci_context.analysis.matcher import HistoricalOccurrence
from ci_context.models.run import WorkflowRunInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: SQLite ``datetime('now', '-N days')`` window.  Seven days keeps the cache
#: useful across a working week while bounding the on-disk footprint.
CACHE_TTL_DAYS = 7

#: SQLite's ``datetime('now')`` produces a space-separated "YYYY-MM-DD HH:MM:SS"
#: timestamp.  We use it as the canonical created_at format because it sorts
#: lexicographically and round-trips through ``datetime('now', '-N days')``
#: arithmetic without any conversion glue.
_SQLITE_NOW = "datetime('now')"


# ---------------------------------------------------------------------------
# Stats dataclass — typed view of what `stats()` returns
# ---------------------------------------------------------------------------


@dataclass
class CacheStats:
    """Snapshot of cache size and contents, returned by :func:`stats`."""

    fingerprint_count: int
    occurrence_count: int
    run_metadata_count: int
    db_size_bytes: int
    db_path: str


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def cache_path() -> Path:
    """Return the canonical path to the cache database.

    Platform convention mirrors the *cache* (not *config*) standard for each
    OS so a `pip cache purge` style command in the future would not surprise
    the user:

    * POSIX: ``$XDG_CACHE_HOME/ci-context/history.db`` or
      ``~/.cache/ci-context/history.db``
    * Windows: ``%LOCALAPPDATA%\\ci-context\\history.db`` (falling back to
      ``%APPDATA%`` if LOCALAPPDATA is unset — e.g. in some CI containers)

    Tests can override either variable via ``monkeypatch.setenv``.
    """
    if os.name == "nt":
        # LOCALAPPDATA is the standard Windows cache location.  Fall back to
        # APPDATA (roaming) so a stripped-down environment without
        # LOCALAPPDATA still gets a writable path.
        base_str = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        base = Path.home() / "AppData" / "Local" if not base_str else Path(base_str)
        return base / "ci-context" / "history.db"
    # POSIX
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "ci-context" / "history.db"


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def init_db(conn: sqlite3.Connection) -> None:
    """Create the three cache tables if they do not exist.

    Idempotent: ``CREATE TABLE IF NOT EXISTS`` is a no-op when the schema is
    already in place, so callers can run this on every open without paying a
    real cost.  Indexes target the two read paths: occurrence lookup by
    fingerprint (matcher) and run_metadata lookup by (run_id, repo).
    """
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS fingerprints (
            fingerprint         TEXT PRIMARY KEY,
            error_type          TEXT NOT NULL,
            normalized_message  TEXT NOT NULL,
            first_seen_at       TEXT NOT NULL,
            last_seen_at        TEXT NOT NULL,
            created_at          TEXT NOT NULL DEFAULT ({_SQLITE_NOW})
        );

        CREATE TABLE IF NOT EXISTS fingerprint_occurrences (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint     TEXT NOT NULL,
            run_id          INTEGER NOT NULL,
            repo            TEXT NOT NULL,
            commit_message  TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            created_at      TEXT NOT NULL DEFAULT ({_SQLITE_NOW}),
            -- One row per (run, fingerprint, repo): re-observing the same
            -- error inside one run must not fabricate a second occurrence,
            -- or related_runs duplicates and occurrence_count inflates.
            UNIQUE(fingerprint, run_id, repo)
        );
        CREATE INDEX IF NOT EXISTS idx_occurrences_fp
            ON fingerprint_occurrences(fingerprint);
        CREATE INDEX IF NOT EXISTS idx_occurrences_repo_created
            ON fingerprint_occurrences(repo, created_at);

        CREATE TABLE IF NOT EXISTS run_metadata (
            run_id      INTEGER NOT NULL,
            repo        TEXT NOT NULL,
            data_json   TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT ({_SQLITE_NOW}),
            PRIMARY KEY (run_id, repo)
        );
        """
    )
    conn.commit()


@contextlib.contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Open (or create) the cache DB and run :func:`init_db` on it.

    Returns a context manager that yields a connection and *closes* it on
    exit.  Returning a generator-based manager (rather than the raw
    connection) is deliberate: ``sqlite3.Connection.__exit__`` only
    commits or rolls back the current transaction, it does not close the
    connection — leaving the caller responsible would let file handles
    leak (notably on Windows, where a held handle blocks tempdir cleanup
    and the cache file itself from being replaced on a future call).

    Behaviour on a corrupted cache file: log a warning, delete the file, and
    open a fresh database.  The CLI must never crash because the cache went
    bad — the cache is a performance optimisation, not a source of truth.

    Recommended pattern::

        with get_connection() as conn, conn:
            conn.execute(...)

    The inner ``with conn:`` provides transactional semantics (commit on
    success, rollback on exception); the outer ``with`` then closes the
    connection regardless of outcome.
    """
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _open_or_recover(path)
    try:
        yield conn
    finally:
        conn.close()


def _open_or_recover(path: Path) -> sqlite3.Connection:
    """Open the cache DB at ``path``; recreate it on corruption.

    Split out from :func:`get_connection` so the context-manager body stays
    linear and the recovery branch is testable in isolation.
    """
    if path.exists():
        try:
            conn = sqlite3.connect(str(path))
            init_db(conn)
            return conn
        except sqlite3.DatabaseError as exc:
            # File exists but is not a valid SQLite database.  Close, delete,
            # and start over — the cache is best-effort by design.
            logger.warning("Cache DB at %s is corrupted (%s); recreating", path, exc)
            with contextlib.suppress(sqlite3.Error):
                conn.close()
            try:
                path.unlink()
            except OSError as unlink_exc:
                logger.warning("Could not delete corrupted cache %s: %s", path, unlink_exc)

    conn = sqlite3.connect(str(path))
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# Fingerprint storage
# ---------------------------------------------------------------------------


def store_fingerprint(
    fingerprint: str,
    error_type: str,
    normalized_message: str,
    run_id: int,
    repo: str,
    commit_message: str,
    timestamp: str,
) -> None:
    """Record a fingerprint occurrence in the cache.

    UPSERT semantics on the ``fingerprints`` row keeps the most recent
    ``last_seen_at`` and bumps the ``created_at`` clock (re-anchoring the TTL
    on every observation).  A new row is appended to
    ``fingerprint_occurrences`` for the history matcher to see the per-run
    trail — unless that exact (run, fingerprint, repo) was already observed,
    in which case the UNIQUE constraint turns the insert into a no-op so a
    repeated observation cannot double-count the same run.

    Args:
        fingerprint: 16-char hex fingerprint from
            :func:`ci_context.analysis.fingerprint.compute_fingerprint`.
        error_type: ``ExtractedError.error_type`` value (e.g. "Python Traceback").
        normalized_message: the *user-facing* error message — stored so a
            future feature can synthesise an ExtractedError without the raw
            log.
        run_id: GitHub Actions run id where the error was observed.
        repo: ``owner/repo`` string; scopes the occurrence to a repo so a
            fingerprint seen in repo A is not confused with one in repo B.
        commit_message: commit message of the run, for matcher commit-pattern
            hints.
        timestamp: ISO-8601 ``Z`` string for when the run happened (not when
            the row was cached).
    """
    with get_connection() as conn, conn:
        conn.execute(
            """
            INSERT INTO fingerprints (
                fingerprint, error_type, normalized_message,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                -- Re-anchor the TTL on every observation so a hot
                -- fingerprint never expires while still being seen.
                created_at = excluded.created_at
            """,
            (fingerprint, error_type, normalized_message, timestamp, timestamp),
        )
        conn.execute(
            """
            INSERT INTO fingerprint_occurrences (
                fingerprint, run_id, repo, commit_message, timestamp
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint, run_id, repo) DO NOTHING
            """,
            (fingerprint, run_id, repo, commit_message, timestamp),
        )


def get_fingerprint_occurrences(
    repo: str | None = None,
    fingerprints: list[str] | None = None,
) -> dict[str, list[HistoricalOccurrence]]:
    """Return cached occurrences of fingerprints, scoped by TTL.

    Args:
        repo: If set, only occurrences for this ``owner/repo`` are returned.
            Other repos' data is filtered out so the history matcher never
            conflates distinct projects.
        fingerprints: If set, restrict the result to these fingerprints.
            ``None`` means "all fingerprints" — used by the matcher's
            initialisation pass before any specific fingerprint is known.

    Returns:
        Mapping of ``fingerprint -> [HistoricalOccurrence]`` ordered oldest
        first.  Fingerprints with no live (non-expired) occurrences are
        omitted, so a fingerprint not in the result is indistinguishable
        from one that has never been seen.
    """
    with get_connection() as conn:
        # Build the WHERE clause incrementally so unused filters don't add
        # a NULL-check overhead.  `?1` / `?2` placeholders reference the
        # optional positional binds below.
        clauses = ["o.created_at >= datetime('now', ?1)"]
        params: list[object] = [f"-{CACHE_TTL_DAYS} days"]

        if repo is not None:
            clauses.append("o.repo = ?2")
            params.append(repo)
            fp_placeholder_start = 3
        else:
            fp_placeholder_start = 2

        if fingerprints:
            placeholders = ",".join(
                f"?{fp_placeholder_start + i}" for i in range(len(fingerprints))
            )
            clauses.append(f"o.fingerprint IN ({placeholders})")
            params.extend(fingerprints)

        sql = f"""
            SELECT o.fingerprint, o.run_id, o.commit_message, o.timestamp
            FROM fingerprint_occurrences o
            WHERE {' AND '.join(clauses)}
            ORDER BY o.timestamp ASC
        """
        rows = conn.execute(sql, params).fetchall()

    result: dict[str, list[HistoricalOccurrence]] = {}
    for fp, run_id, commit_message, timestamp in rows:
        result.setdefault(fp, []).append(
            HistoricalOccurrence(
                run_id=run_id,
                timestamp=timestamp,
                commit_message=commit_message,
            )
        )
    return result


# ---------------------------------------------------------------------------
# Run metadata storage
# ---------------------------------------------------------------------------


def _serialize_run(run: WorkflowRunInfo) -> str:
    """Serialise a WorkflowRunInfo to a JSON string for run_metadata.data_json.

    WorkflowRunInfo carries a ``datetime`` which ``json.dumps`` cannot
    serialise by default.  ISO-format it explicitly so a future
    ``json.loads`` round-trip yields an equivalent value.
    """
    payload = asdict(run)
    payload["created_at"] = run.created_at.isoformat()
    return json.dumps(payload)


def _deserialize_run(data_json: str) -> WorkflowRunInfo:
    """Inverse of :func:`_serialize_run`; parses ``created_at`` back to datetime."""
    payload = json.loads(data_json)
    payload["created_at"] = datetime.fromisoformat(payload["created_at"])
    return WorkflowRunInfo(**payload)


def store_run_metadata(run_id: int, repo: str, run: WorkflowRunInfo) -> None:
    """Cache a ``WorkflowRunInfo`` keyed by ``(run_id, repo)``.

    UPSERT semantics: re-caching the same run refreshes both the payload
    and the TTL anchor.  Note that the stored object is a *snapshot* — if
    the run later changes state on GitHub, the cache will not know until
    the caller overwrites it.
    """
    with get_connection() as conn, conn:
        conn.execute(
            """
            INSERT INTO run_metadata (run_id, repo, data_json)
            VALUES (?, ?, ?)
            ON CONFLICT(run_id, repo) DO UPDATE SET
                data_json = excluded.data_json,
                created_at = excluded.created_at
            """,
            (run_id, repo, _serialize_run(run)),
        )


def get_run_metadata(run_id: int, repo: str) -> WorkflowRunInfo | None:
    """Return a cached ``WorkflowRunInfo`` or ``None`` if missing / expired.

    ``None`` is intentionally indistinguishable from "expired" so callers
    cannot accidentally surface stale data by treating a miss as success.
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT data_json FROM run_metadata
            WHERE run_id = ? AND repo = ?
              AND created_at >= datetime('now', ?)
            """,
            (run_id, repo, f"-{CACHE_TTL_DAYS} days"),
        ).fetchone()
    if row is None:
        return None
    return _deserialize_run(row[0])


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


def clear() -> int:
    """Delete every row from every table.  Returns the number removed.

    Idempotent: calling on an already-empty cache returns 0.  The DB file
    itself is left in place (with the schema) so the next read does not
    have to run :func:`init_db` from scratch.
    """
    with get_connection() as conn, conn:
        total = 0
        for table in ("fingerprint_occurrences", "fingerprints", "run_metadata"):
            cur = conn.execute(f"DELETE FROM {table}")
            total += cur.rowcount
    return total


def purge_expired() -> int:
    """Delete rows whose ``created_at`` is older than the TTL.  Returns the count.

    Optional companion to lazy expiry: callers that want to reclaim disk
    space without dropping the whole cache can run this periodically.
    The CLI does not invoke it automatically.
    """
    with get_connection() as conn, conn:
        total = 0
        for table in ("fingerprint_occurrences", "fingerprints", "run_metadata"):
            cur = conn.execute(
                f"DELETE FROM {table} WHERE created_at < datetime('now', ?)",
                (f"-{CACHE_TTL_DAYS} days",),
            )
            total += cur.rowcount
    return total


def stats() -> CacheStats:
    """Return a snapshot of cache contents and on-disk size.

    Used by the future ``ci-context cache stats`` command (D25) and as a
    quick smoke test that the DB is reachable.  ``db_size_bytes`` is the
    raw file size — SQLite may report slightly different "used" sizes
    internally after a VACUUM, but the on-disk number is what the user
    cares about.
    """
    with get_connection() as conn:
        fingerprint_count = conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
        occurrence_count = conn.execute(
            "SELECT COUNT(*) FROM fingerprint_occurrences"
        ).fetchone()[0]
        run_metadata_count = conn.execute("SELECT COUNT(*) FROM run_metadata").fetchone()[0]

    path = cache_path()
    try:
        db_size_bytes = path.stat().st_size
    except OSError:
        # Cache file might not exist yet on a brand-new install — treat as 0
        # rather than failing the stats command.
        db_size_bytes = 0

    return CacheStats(
        fingerprint_count=fingerprint_count,
        occurrence_count=occurrence_count,
        run_metadata_count=run_metadata_count,
        db_size_bytes=db_size_bytes,
        db_path=str(path),
    )


# ---------------------------------------------------------------------------
# Helpers exported for tests
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 ``Z`` string.

    The fingerprint occurrence ``timestamp`` column is the *event* time,
    not the cache time; this helper exists so tests and CLI callers can
    produce the same format the matcher expects from a fresh run.
    """
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

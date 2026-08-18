"""Tests for the SQLite cache layer (cache/db.py).

Coverage map:
    * :class:`TestCachePath`             — env-var resolution per platform
    * :class:`TestInitDb`                — schema creation, idempotency
    * :class:`TestStoreAndGetFingerprint`— round-trip, UPSERT, repo scoping
    * :class:`TestStoreAndGetRunMetadata`— round-trip, TTL filter
    * :class:`TestTtl`                   — expiry semantics + purge_expired
    * :class:`TestClearAndStats`         — clear() and stats() shape
    * :class:`TestCorruptedDb`           — graceful recovery on bad file
    * :class:`TestHistoricalOccurrenceRoundTrip` — drops straight into matcher

Tests use a per-test temp directory for the cache file so they never touch
the real ``~/.cache/ci-context`` or ``%LOCALAPPDATA%\\ci-context``.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

from ci_context.analysis.fingerprint import compute_fingerprint
from ci_context.analysis.matcher import HistoricalOccurrence, match_errors
from ci_context.cache.db import (
    CACHE_TTL_DAYS,
    CacheStats,
    cache_path,
    clear,
    get_connection,
    get_fingerprint_occurrences,
    get_run_metadata,
    init_db,
    purge_expired,
    stats,
    store_fingerprint,
    store_run_metadata,
)
from ci_context.models.error import ExtractedError
from ci_context.models.run import WorkflowRunInfo

# ---------------------------------------------------------------------------
# Test fixtures and helpers
# ---------------------------------------------------------------------------

_DEFAULT_REPO = "owner/repo"


def _run(
    run_id: int = 12345,
    repo: str = _DEFAULT_REPO,
    *,
    status: str = "completed",
    conclusion: str | None = "failure",
) -> WorkflowRunInfo:
    """Shorthand to build a minimal WorkflowRunInfo for run_metadata tests."""
    from datetime import UTC, datetime

    return WorkflowRunInfo(
        id=run_id,
        status=status,
        conclusion=conclusion,
        workflow_name="CI",
        head_sha="abcdef1234567890",
        event="push",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        url=f"https://github.com/{repo}/actions/runs/{run_id}",
        attempt=1,
        duration_seconds=42.0,
    )


def _error(
    message: str = "division by zero", error_type: str = "Python Traceback"
) -> ExtractedError:
    return ExtractedError(error_type=error_type, message=message)


@contextlib.contextmanager
def _temp_cache_dir() -> Iterator[Path]:
    """Redirect the cache to a temp dir for the duration of a test.

    Patches both the XDG / LOCALAPPDATA env vars (so ``cache_path()`` returns
    a path inside the temp dir) and ``Path.home`` (so the fallbacks also land
    in the temp dir on machines with no env var set).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        env_patch = {
            "XDG_CACHE_HOME": str(tmp_path),
            "LOCALAPPDATA": str(tmp_path),
            "APPDATA": str(tmp_path),
        }
        with patch.dict(os.environ, env_patch, clear=False), patch.object(
            Path, "home", classmethod(lambda cls: tmp_path)
        ):
            yield tmp_path


# ---------------------------------------------------------------------------
# TestCachePath
# ---------------------------------------------------------------------------


class TestCachePath(unittest.TestCase):
    """``cache_path()`` honours platform env vars and falls back to $HOME.

    The Path class is bound to the *actual* OS at import time
    (``WindowsPath`` on Windows, ``PosixPath`` elsewhere), so we cannot
    drive the other branch on the current platform by patching ``os.name``
    — ``Path()`` would try to instantiate a foreign class and raise
    ``NotImplementedError``.  Instead we test the platform-matching branch
    here and exercise the cross-platform join logic via string comparisons.
    """

    def test_uses_xdg_cache_home_when_present(self) -> None:
        # XDG_CACHE_HOME is only read on POSIX; on Windows the function
        # reads LOCALAPPDATA.  Skip on Windows so the assertion below
        # actually targets the XDG branch.
        if os.name == "nt":
            self.skipTest("XDG_CACHE_HOME only consulted on POSIX")
        with tempfile.TemporaryDirectory() as xdg:
            xdg_path = Path(xdg)
            with patch.dict(os.environ, {"XDG_CACHE_HOME": str(xdg_path)}, clear=False):
                result = cache_path()
            self.assertEqual(result.parent.parent, xdg_path)
            self.assertEqual(result.name, "history.db")
            self.assertEqual(result.parent.name, "ci-context")

    def test_posix_falls_back_to_home(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX path class not available on Windows")
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            with patch.dict(os.environ, {}, clear=True), patch.object(
                Path, "home", classmethod(lambda cls: home_path)
            ):
                self.assertEqual(
                    cache_path(), home_path / ".cache" / "ci-context" / "history.db"
                )

    def test_windows_uses_localappdata(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows-specific test")
        with tempfile.TemporaryDirectory() as local_app:
            local_path = Path(local_app)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_path)}, clear=False):
                self.assertEqual(
                    cache_path(), local_path / "ci-context" / "history.db"
                )

    def test_windows_falls_back_to_appdata(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows-specific test")
        with tempfile.TemporaryDirectory() as roaming:
            roaming_path = Path(roaming)
            with patch.dict(os.environ, {"APPDATA": str(roaming_path)}, clear=True):
                self.assertEqual(
                    cache_path(), roaming_path / "ci-context" / "history.db"
                )

    def test_windows_falls_back_to_home(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows-specific test")
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            with patch.dict(os.environ, {}, clear=True), patch.object(
                Path, "home", classmethod(lambda cls: home_path)
            ):
                self.assertEqual(
                    cache_path(),
                    home_path / "AppData" / "Local" / "ci-context" / "history.db",
                )


# ---------------------------------------------------------------------------
# TestInitDb
# ---------------------------------------------------------------------------


class TestInitDb(unittest.TestCase):
    """init_db() creates the schema and is idempotent."""

    def setUp(self) -> None:
        self._cm = _temp_cache_dir()
        self._cm.__enter__()

    def tearDown(self) -> None:
        self._cm.__exit__(None, None, None)

    def test_creates_all_tables(self) -> None:
        with get_connection() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertEqual(
            tables,
            {"fingerprints", "fingerprint_occurrences", "run_metadata", "sqlite_sequence"},
        )

    def test_creates_expected_indexes(self) -> None:
        with get_connection() as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
        self.assertIn("idx_occurrences_fp", indexes)
        self.assertIn("idx_occurrences_repo_created", indexes)

    def test_occurrence_unique_index_covers_run_fp_repo(self) -> None:
        # The schema must forbid two occurrence rows for the same
        # (run, fingerprint, repo); otherwise store() called twice on one run
        # would inflate occurrence_count for the matcher.
        with get_connection() as conn:
            index_list = conn.execute(
                "PRAGMA index_list('fingerprint_occurrences')"
            ).fetchall()
        # PRAGMA index_list rows: (seq, name, unique, origin, partial). origin
        # "u" is an index created by a UNIQUE constraint (as opposed to our
        # explicit CREATE INDEX statements, origin "c").
        unique_idx = [row for row in index_list if row[2] == 1 and row[3] == "u"]
        self.assertEqual(len(unique_idx), 1)
        name = unique_idx[0][1]
        with get_connection() as conn:
            cols = {
                row[2] for row in conn.execute(f"PRAGMA index_info('{name}')").fetchall()
            }
        self.assertEqual(cols, {"fingerprint", "run_id", "repo"})

    def test_idempotent(self) -> None:
        # Running init_db twice in a row should not raise and should keep
        # the schema intact (no duplicates, no errors).
        with get_connection() as conn:
            init_db(conn)
            init_db(conn)
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        # 3 application tables + sqlite_sequence = 4
        self.assertEqual(count, 4)

    def test_creates_parent_directory(self) -> None:
        # Aim cache_path() at a path whose parent does not yet exist, then
        # call get_connection() and verify the parent now exists.  The
        # temp dir's `ci-context` subdir is created as a side effect of
        # opening the connection.
        target_dir = Path(tempfile.gettempdir()) / "ci-context-test-mkdir"
        # Clean up any leftover from a previous run before the test starts.
        if target_dir.exists():
            import shutil

            shutil.rmtree(target_dir)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(target_dir)}, clear=True), patch(
            "ci_context.cache.db.os.name", "nt"
        ):
            path = cache_path()
            self.assertFalse(path.parent.exists())
            with get_connection():
                pass
            self.assertTrue(path.parent.is_dir())
        # Cleanup
        if target_dir.exists():
            import shutil

            shutil.rmtree(target_dir)


# ---------------------------------------------------------------------------
# TestStoreAndGetFingerprint
# ---------------------------------------------------------------------------


class TestStoreAndGetFingerprint(unittest.TestCase):
    """Round-trip + UPSERT + repo scoping for fingerprint occurrences."""

    def setUp(self) -> None:
        self._cm = _temp_cache_dir()
        self._cm.__enter__()
        # Start each test from a clean cache so leftover rows from one test
        # don't leak into the next.
        clear()

    def tearDown(self) -> None:
        clear()
        self._cm.__exit__(None, None, None)

    def test_store_then_get_round_trip(self) -> None:
        err = _error("division by zero")
        fp = compute_fingerprint(err)

        store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=100,
            repo=_DEFAULT_REPO,
            commit_message="fix bug",
            timestamp="2025-01-01T00:00:00Z",
        )

        result = get_fingerprint_occurrences(repo=_DEFAULT_REPO)
        self.assertIn(fp, result)
        self.assertEqual(len(result[fp]), 1)
        occ = result[fp][0]
        self.assertEqual(occ.run_id, 100)
        self.assertEqual(occ.commit_message, "fix bug")
        self.assertEqual(occ.timestamp, "2025-01-01T00:00:00Z")

    def test_store_appends_occurrences(self) -> None:
        # Two observations of the same fingerprint on different runs produce
        # two occurrence rows, not a single overwriting one.
        err = _error("division by zero")
        fp = compute_fingerprint(err)

        store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=100,
            repo=_DEFAULT_REPO,
            commit_message="first",
            timestamp="2025-01-01T00:00:00Z",
        )
        store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=101,
            repo=_DEFAULT_REPO,
            commit_message="second",
            timestamp="2025-01-02T00:00:00Z",
        )

        result = get_fingerprint_occurrences(repo=_DEFAULT_REPO)
        self.assertEqual(len(result[fp]), 2)
        self.assertEqual([o.run_id for o in result[fp]], [100, 101])

    def test_store_same_run_does_not_duplicate_occurrence(self) -> None:
        # Two store() calls for the same (run, fp, repo) are the extractor
        # re-observing one error inside a single run — that must count once,
        # or occurrence_count inflates and related_runs duplicates the run id.
        err = _error("division by zero")
        fp = compute_fingerprint(err)

        for _ in range(2):
            store_fingerprint(
                fingerprint=fp,
                error_type=err.error_type,
                normalized_message=err.message,
                run_id=100,
                repo=_DEFAULT_REPO,
                commit_message="fix bug",
                timestamp="2025-01-01T00:00:00Z",
            )

        result = get_fingerprint_occurrences(repo=_DEFAULT_REPO)
        self.assertIn(fp, result)
        self.assertEqual(len(result[fp]), 1)
        self.assertEqual(result[fp][0].run_id, 100)
        with get_connection() as conn:
            count_occ = conn.execute(
                "SELECT COUNT(*) FROM fingerprint_occurrences"
            ).fetchone()[0]
        self.assertEqual(count_occ, 1)

    def test_upsert_keeps_one_fingerprint_row(self) -> None:
        # Storing the same fingerprint twice should leave a single row in
        # the `fingerprints` table (PRIMARY KEY conflict) but two rows in
        # the append-only occurrences table.
        err = _error("division by zero")
        fp = compute_fingerprint(err)

        for run_id in (200, 201):
            store_fingerprint(
                fingerprint=fp,
                error_type=err.error_type,
                normalized_message=err.message,
                run_id=run_id,
                repo=_DEFAULT_REPO,
                commit_message="m",
                timestamp=f"2025-01-{run_id - 199:02d}T00:00:00Z",
            )

        with get_connection() as conn:
            count_fp = conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
            count_occ = conn.execute(
                "SELECT COUNT(*) FROM fingerprint_occurrences"
            ).fetchone()[0]
        self.assertEqual(count_fp, 1)
        self.assertEqual(count_occ, 2)

    def test_get_filters_by_repo(self) -> None:
        # Occurrences stored under repo A must not leak into repo B queries.
        err = _error("shared error")
        fp = compute_fingerprint(err)

        store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=1,
            repo="owner/repoA",
            commit_message="x",
            timestamp="2025-01-01T00:00:00Z",
        )
        store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=2,
            repo="owner/repoB",
            commit_message="y",
            timestamp="2025-01-02T00:00:00Z",
        )

        a = get_fingerprint_occurrences(repo="owner/repoA")
        b = get_fingerprint_occurrences(repo="owner/repoB")
        self.assertEqual([o.run_id for o in a[fp]], [1])
        self.assertEqual([o.run_id for o in b[fp]], [2])

    def test_get_filters_by_fingerprint_list(self) -> None:
        # When a fingerprint list is provided, occurrences for other
        # fingerprints are not returned.
        e1 = _error("first error")
        e2 = _error("second error")
        fp1, fp2 = compute_fingerprint(e1), compute_fingerprint(e2)

        for fp, err, run_id in (
            (fp1, e1, 10),
            (fp2, e2, 11),
        ):
            store_fingerprint(
                fingerprint=fp,
                error_type=err.error_type,
                normalized_message=err.message,
                run_id=run_id,
                repo=_DEFAULT_REPO,
                commit_message="m",
                timestamp="2025-01-01T00:00:00Z",
            )

        result = get_fingerprint_occurrences(repo=_DEFAULT_REPO, fingerprints=[fp1])
        self.assertEqual(list(result.keys()), [fp1])

    def test_get_returns_ordered_occurrences(self) -> None:
        # match_errors() indexes occs[0] and occs[-1] as first/last seen, so
        # ordering by timestamp ASC is a hard correctness requirement.
        err = _error("order me")
        fp = compute_fingerprint(err)

        for run_id, ts in [
            (30, "2025-03-01T00:00:00Z"),
            (31, "2025-01-01T00:00:00Z"),
            (32, "2025-02-01T00:00:00Z"),
        ]:
            store_fingerprint(
                fingerprint=fp,
                error_type=err.error_type,
                normalized_message=err.message,
                run_id=run_id,
                repo=_DEFAULT_REPO,
                commit_message="m",
                timestamp=ts,
            )

        result = get_fingerprint_occurrences(repo=_DEFAULT_REPO)
        timestamps = [o.timestamp for o in result[fp]]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_get_no_args_returns_all(self) -> None:
        # No repo / no fingerprint list => return everything (for the
        # matcher's "scan all known fingerprints" path).
        e1 = _error("alpha")
        e2 = _error("beta")
        for err in (e1, e2):
            fp = compute_fingerprint(err)
            store_fingerprint(
                fingerprint=fp,
                error_type=err.error_type,
                normalized_message=err.message,
                run_id=1,
                repo=_DEFAULT_REPO,
                commit_message="m",
                timestamp="2025-01-01T00:00:00Z",
            )

        all_result = get_fingerprint_occurrences()
        self.assertEqual(len(all_result), 2)


# ---------------------------------------------------------------------------
# TestStoreAndGetRunMetadata
# ---------------------------------------------------------------------------


class TestStoreAndGetRunMetadata(unittest.TestCase):
    """WorkflowRunInfo round-trip."""

    def setUp(self) -> None:
        self._cm = _temp_cache_dir()
        self._cm.__enter__()
        clear()

    def tearDown(self) -> None:
        clear()
        self._cm.__exit__(None, None, None)

    def test_round_trip(self) -> None:
        run = _run(run_id=42)
        store_run_metadata(42, _DEFAULT_REPO, run)

        cached = get_run_metadata(42, _DEFAULT_REPO)
        self.assertIsNotNone(cached)
        assert cached is not None
        self.assertEqual(cached, run)
        # Sanity: nested datetime survives the JSON round-trip
        self.assertEqual(cached.created_at, run.created_at)

    def test_missing_returns_none(self) -> None:
        self.assertIsNone(get_run_metadata(999, _DEFAULT_REPO))

    def test_upsert_overwrites(self) -> None:
        run1 = _run(run_id=1, status="in_progress", conclusion=None)
        run2 = _run(run_id=1, status="completed", conclusion="failure")

        store_run_metadata(1, _DEFAULT_REPO, run1)
        store_run_metadata(1, _DEFAULT_REPO, run2)

        cached = get_run_metadata(1, _DEFAULT_REPO)
        self.assertEqual(cached, run2)

    def test_keyed_by_repo(self) -> None:
        # Same run id under different repos must not collide.
        run_a = _run(run_id=7, repo="owner/repoA")
        run_b = _run(run_id=7, repo="owner/repoB", conclusion="success")
        store_run_metadata(7, "owner/repoA", run_a)
        store_run_metadata(7, "owner/repoB", run_b)

        self.assertEqual(get_run_metadata(7, "owner/repoA"), run_a)
        self.assertEqual(get_run_metadata(7, "owner/repoB"), run_b)

    def test_serialised_format_is_json(self) -> None:
        # The data_json column is JSON; future debug tooling can `jq` it
        # without re-parsing Python repr.  Pin the format here so a change
        # is a deliberate decision.
        run = _run()
        store_run_metadata(run.id, _DEFAULT_REPO, run)

        with get_connection() as conn:
            row = conn.execute(
                "SELECT data_json FROM run_metadata WHERE run_id = ?",
                (run.id,),
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        parsed = json.loads(row[0])
        self.assertEqual(parsed["id"], run.id)
        self.assertEqual(parsed["workflow_name"], run.workflow_name)
        # created_at is ISO-formatted (json.dumps cannot handle datetime)
        self.assertIn("T", parsed["created_at"])


# ---------------------------------------------------------------------------
# TestTtl
# ---------------------------------------------------------------------------


class TestTtl(unittest.TestCase):
    """Lazy TTL expiry: old rows are invisible to reads."""

    def setUp(self) -> None:
        self._cm = _temp_cache_dir()
        self._cm.__enter__()
        clear()

    def tearDown(self) -> None:
        clear()
        self._cm.__exit__(None, None, None)

    def test_ttl_constant_is_seven_days(self) -> None:
        # Pin the documented TTL so a change forces a test update.
        self.assertEqual(CACHE_TTL_DAYS, 7)

    def test_fingerprint_occurrence_hidden_when_old(self) -> None:
        # Insert a row whose created_at is older than 7 days; a fresh read
        # should NOT return it.
        err = _error("stale")
        fp = compute_fingerprint(err)

        store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=1,
            repo=_DEFAULT_REPO,
            commit_message="old",
            timestamp="2024-01-01T00:00:00Z",
        )

        # Rewind the created_at column to 8 days ago so the TTL filter
        # treats it as expired.  In production this would never happen
        # organically — the row is created at the time of writing — but
        # it lets us deterministically test the expiry code path.
        eight_days_ago = "datetime('now', '-8 days')"
        with get_connection() as conn, conn:
            conn.execute(
                "UPDATE fingerprint_occurrences SET created_at = " + eight_days_ago
            )
            conn.execute(
                "UPDATE fingerprints SET created_at = " + eight_days_ago
            )

        result = get_fingerprint_occurrences(repo=_DEFAULT_REPO)
        self.assertNotIn(fp, result)

    def test_run_metadata_hidden_when_old(self) -> None:
        run = _run(run_id=1)
        store_run_metadata(1, _DEFAULT_REPO, run)

        with get_connection() as conn, conn:
            conn.execute(
                "UPDATE run_metadata SET created_at = datetime('now', '-8 days')"
            )

        self.assertIsNone(get_run_metadata(1, _DEFAULT_REPO))

    def test_purge_expired_removes_old_rows(self) -> None:
        err = _error("to be purged")
        fp = compute_fingerprint(err)
        run = _run(run_id=2)

        store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=2,
            repo=_DEFAULT_REPO,
            commit_message="m",
            timestamp="2024-01-01T00:00:00Z",
        )
        store_run_metadata(2, _DEFAULT_REPO, run)

        with get_connection() as conn, conn:
            conn.execute(
                "UPDATE fingerprint_occurrences SET created_at = datetime('now', '-8 days')"
            )
            conn.execute(
                "UPDATE fingerprints SET created_at = datetime('now', '-8 days')"
            )
            conn.execute(
                "UPDATE run_metadata SET created_at = datetime('now', '-8 days')"
            )

        removed = purge_expired()
        self.assertGreaterEqual(removed, 3)  # 1 fp + 1 occurrence + 1 run row

        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0], 0)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM fingerprint_occurrences").fetchone()[0], 0
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM run_metadata").fetchone()[0], 0)

    def test_purge_expired_keeps_fresh_rows(self) -> None:
        # A row created now (fresh created_at) must survive purge_expired.
        err = _error("keep me")
        fp = compute_fingerprint(err)
        store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=3,
            repo=_DEFAULT_REPO,
            commit_message="m",
            timestamp="2025-01-01T00:00:00Z",
        )

        removed = purge_expired()
        self.assertEqual(removed, 0)

        result = get_fingerprint_occurrences(repo=_DEFAULT_REPO)
        self.assertIn(fp, result)


# ---------------------------------------------------------------------------
# TestClearAndStats
# ---------------------------------------------------------------------------


class TestClearAndStats(unittest.TestCase):
    """clear() / stats() contract for the D25 cache commands."""

    def setUp(self) -> None:
        self._cm = _temp_cache_dir()
        self._cm.__enter__()
        clear()

    def tearDown(self) -> None:
        clear()
        self._cm.__exit__(None, None, None)

    def test_stats_empty_cache(self) -> None:
        s = stats()
        self.assertIsInstance(s, CacheStats)
        self.assertEqual(s.fingerprint_count, 0)
        self.assertEqual(s.occurrence_count, 0)
        self.assertEqual(s.run_metadata_count, 0)
        # db_size_bytes is the on-disk file size; even an "empty" SQLite
        # file is > 0 bytes, but on a brand-new install the file may not
        # exist yet so we accept either 0 or a small positive number.
        self.assertGreaterEqual(s.db_size_bytes, 0)
        self.assertTrue(s.db_path.endswith("history.db"))

    def test_stats_reflects_inserts(self) -> None:
        err = _error("count me")
        fp = compute_fingerprint(err)
        store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=1,
            repo=_DEFAULT_REPO,
            commit_message="m",
            timestamp="2025-01-01T00:00:00Z",
        )
        store_run_metadata(1, _DEFAULT_REPO, _run(run_id=1))

        s = stats()
        self.assertEqual(s.fingerprint_count, 1)
        self.assertEqual(s.occurrence_count, 1)
        self.assertEqual(s.run_metadata_count, 1)

    def test_clear_empties_all_tables(self) -> None:
        err = _error("to clear")
        fp = compute_fingerprint(err)
        store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=1,
            repo=_DEFAULT_REPO,
            commit_message="m",
            timestamp="2025-01-01T00:00:00Z",
        )
        store_run_metadata(1, _DEFAULT_REPO, _run(run_id=1))

        removed = clear()
        self.assertEqual(removed, 3)  # 1 fp + 1 occurrence + 1 run row

        s = stats()
        self.assertEqual(s.fingerprint_count, 0)
        self.assertEqual(s.occurrence_count, 0)
        self.assertEqual(s.run_metadata_count, 0)

    def test_clear_is_idempotent(self) -> None:
        clear()
        # A second clear on an already-empty cache is a no-op (returns 0)
        self.assertEqual(clear(), 0)


# ---------------------------------------------------------------------------
# TestCorruptedDb
# ---------------------------------------------------------------------------


class TestCorruptedDb(unittest.TestCase):
    """A garbage cache file must not crash the CLI — get_connection() recovers."""

    def setUp(self) -> None:
        self._cm = _temp_cache_dir()
        self._cm.__enter__()

    def tearDown(self) -> None:
        self._cm.__exit__(None, None, None)

    def test_corrupted_file_is_replaced(self) -> None:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Plant a file that looks like a DB but is full of random bytes
        # that will fail SQLite's header parse.
        path.write_bytes(b"this is not a sqlite database, just garbage bytes" * 10)

        # The first call detects the corruption and recreates the file.
        # If the recovery logic regressed, this would raise
        # sqlite3.DatabaseError instead of returning a working connection.
        with get_connection() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertIn("fingerprints", tables)

        # And subsequent operations work end-to-end.
        store_fingerprint(
            fingerprint="deadbeef" * 2,  # 16 hex chars
            error_type="Test",
            normalized_message="after recovery",
            run_id=1,
            repo=_DEFAULT_REPO,
            commit_message="m",
            timestamp="2025-01-01T00:00:00Z",
        )
        result = get_fingerprint_occurrences(repo=_DEFAULT_REPO)
        self.assertIn("deadbeef" * 2, result)

    def test_empty_file_is_treated_as_fresh(self) -> None:
        # An empty (zero-byte) file is valid for sqlite3.connect — it
        # creates a fresh DB.  Verify init_db still runs.
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM fingerprints"
            ).fetchone()[0]
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------------
# TestHistoricalOccurrenceRoundTrip
# ---------------------------------------------------------------------------


class TestHistoricalOccurrenceRoundTrip(unittest.TestCase):
    """The dict returned by get_fingerprint_occurrences plugs into matcher unchanged."""

    def setUp(self) -> None:
        self._cm = _temp_cache_dir()
        self._cm.__enter__()
        clear()

    def tearDown(self) -> None:
        clear()
        self._cm.__exit__(None, None, None)

    def test_returned_value_feeds_match_errors(self) -> None:
        # End-to-end: store, fetch, hand off to match_errors, verify the
        # match_type is "exact" — this is the exact path the future gh.py
        # integration will use.
        err = _error("division by zero")
        fp = compute_fingerprint(err)

        store_fingerprint(
            fingerprint=fp,
            error_type=err.error_type,
            normalized_message=err.message,
            run_id=500,
            repo=_DEFAULT_REPO,
            commit_message="past failure",
            timestamp="2025-01-01T00:00:00Z",
        )

        historical: dict[str, list[HistoricalOccurrence]] = get_fingerprint_occurrences(
            repo=_DEFAULT_REPO
        )
        results = match_errors([err], historical)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].match_type, "exact")
        self.assertEqual(results[0].related_runs, [500])


if __name__ == "__main__":
    unittest.main()

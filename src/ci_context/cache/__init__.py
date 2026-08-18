"""SQLite cache package.

Re-exports the public API of :mod:`ci_context.cache.db` so callers can write
``from ci_context.cache import store_fingerprint, get_connection, ...``
without reaching into the implementation module.
"""

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

__all__ = [
    "CACHE_TTL_DAYS",
    "CacheStats",
    "cache_path",
    "clear",
    "get_connection",
    "get_fingerprint_occurrences",
    "get_run_metadata",
    "init_db",
    "purge_expired",
    "stats",
    "store_fingerprint",
    "store_run_metadata",
]

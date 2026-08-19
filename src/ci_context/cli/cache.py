"""`ci-context cache` sub-command group — cache management."""

from __future__ import annotations

from io import StringIO

import typer
from rich.console import Console
from rich.table import Table

from ci_context.cache import db

# Status / progress / errors go to stderr; the rendered Rich table for
# `stats` goes to stdout.  Same stdout/stderr split as gh.py, so the two
# sub-command families behave identically when piped.
console = Console(stderr=True)

cache_app = typer.Typer(
    name="cache",
    help="Cache management commands.",
    no_args_is_help=True,
)


def _humanise_bytes(n: int) -> str:
    """Return ``n`` formatted with a human-friendly KiB/MiB suffix.

    Raw byte counts are unhelpful for the typical <100 KiB cache file; the
    one-decimal precision is chosen so 1.5 KiB reads as ``1.5 KiB`` rather
    than rounding into ``2 KiB`` and obscuring the actual size.
    """
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"


@cache_app.command("clear")
def cache_clear() -> None:
    """Clear the local cache."""
    # clear() returns the number of rows deleted across the three tables;
    # the count gives the user a sanity check that something was actually
    # removed (vs. an idempotent call on an already-empty cache).
    try:
        removed = db.clear()
        console.print(f"Cache cleared: {removed} row(s) removed.")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e


@cache_app.command("stats")
def cache_stats() -> None:
    """Show cache statistics."""
    try:
        s = db.stats()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    # Render to a StringIO buffer and echo via typer.echo so stdout carries
    # the same "one rendered block" contract as gh recent/repo.
    # Force terminal off so piped output is plain text.
    buffer = StringIO()
    out_console = Console(file=buffer, force_terminal=False, width=100)
    table = Table(title="Cache Statistics", header_style="bold magenta", show_lines=False)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value")
    table.add_row("Fingerprints", str(s.fingerprint_count))
    table.add_row("Fingerprint occurrences", str(s.occurrence_count))
    table.add_row("Run metadata entries", str(s.run_metadata_count))
    table.add_row("Database size", _humanise_bytes(s.db_size_bytes))
    table.add_row("Database path", s.db_path)
    out_console.print(table)
    typer.echo(buffer.getvalue())


@cache_app.command("purge")
def cache_purge() -> None:
    """Delete expired cache entries (older than the TTL)."""
    # purge_expired() removes only TTL-expired rows, leaving the schema and
    # any fresh data intact.  Distinct from `clear`, which empties the
    # entire cache regardless of age.
    try:
        removed = db.purge_expired()
        console.print(f"Expired cache entries purged: {removed} row(s) removed.")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

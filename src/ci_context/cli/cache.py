"""`ci-context cache` sub-command group — cache management."""

import typer

cache_app = typer.Typer(
    name="cache",
    help="Cache management commands.",
    no_args_is_help=True,
)


@cache_app.command("clear")
def cache_clear() -> None:
    """Clear the local cache."""
    # TODO: implement (Week 4)
    typer.echo("Cache cleared. (not yet implemented)")


@cache_app.command("stats")
def cache_stats() -> None:
    """Show cache statistics."""
    # TODO: implement (Week 4)
    typer.echo("Cache stats: (not yet implemented)")

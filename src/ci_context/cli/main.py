"""Root CLI entry point — Typer app and global options."""

from typing import Optional

import typer

from ci_context import __version__

app = typer.Typer(
    name="ci-context",
    help="One command to get full CI failure context.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Register sub-command groups (imported lazily to avoid circular deps at import time)
from ci_context.cli.gh import gh_app  # noqa: E402
from ci_context.cli.cache import cache_app  # noqa: E402

app.add_typer(gh_app, name="gh")
app.add_typer(cache_app, name="cache")


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"ci-context {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """ci-context — One command to get full CI failure context."""

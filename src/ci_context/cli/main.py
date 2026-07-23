"""Root CLI entry point — Typer app and global options."""

import logging

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
from ci_context.cli.cache import cache_app  # noqa: E402
from ci_context.cli.gh import gh_app  # noqa: E402

app.add_typer(gh_app, name="gh")
app.add_typer(cache_app, name="cache")


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"ci-context {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool | None = typer.Option(
        None,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output (DEBUG-level logging).",
    ),
) -> None:
    """ci-context — One command to get full CI failure context."""
    # Store verbose flag for sub-commands to access via ctx.obj
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose

    # Configure logging once at the application root, not per-sub-command.
    # force=True ensures our handler wins even if an imported library already
    # set one up.  DEBUG level in verbose mode exposes all library diagnostics;
    # without --verbose, the root logger stays silent (no handler = no output).
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(levelname)s: %(name)s: %(message)s",
            force=True,
        )

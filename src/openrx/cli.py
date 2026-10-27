"""Command-line interface: the `openrx` command."""

from typing import Annotated

import typer

from openrx import __version__

app = typer.Typer(
    name="openrx",
    help="OpenRx: public FDA/NIH drug data for AI agents, via sandboxed code execution.",
    no_args_is_help=True,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"openrx {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """OpenRx command-line interface."""

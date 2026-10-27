"""Command-line interface: the `openrx` command."""

from pathlib import Path
from typing import Annotated

import typer

from openrx import __version__
from openrx.catalog.loader import SOURCES_DIR, lint_catalog, load_catalog

app = typer.Typer(
    name="openrx",
    help="OpenRx: public FDA/NIH drug data for AI agents, via sandboxed code execution.",
    no_args_is_help=True,
)
catalog_app = typer.Typer(help="Inspect and check the API catalog.", no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")


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


@catalog_app.command("lint")
def catalog_lint(
    directory: Annotated[
        Path, typer.Option("--dir", help="Folder of catalog YAML files.")
    ] = SOURCES_DIR,
) -> None:
    """Check every catalog file; exit with status 1 if anything is wrong."""
    problems = lint_catalog(directory)
    if problems:
        for problem in problems:
            typer.echo(str(problem), err=True)
        files = len({p.file for p in problems})
        typer.echo(f"FAILED: {len(problems)} problem(s) in {files} file(s)", err=True)
        raise typer.Exit(code=1)

    catalog = load_catalog(directory)
    endpoints = sum(len(s.endpoints) for s in catalog)
    typer.echo(f"OK: {len(catalog.sources)} source(s), {endpoints} endpoint(s)")

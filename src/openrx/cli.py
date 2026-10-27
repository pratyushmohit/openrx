"""Command-line interface: the `openrx` command."""

from pathlib import Path
from typing import Annotated

import httpx
import typer

from openrx import __version__
from openrx.catalog.capture import capture_source
from openrx.catalog.loader import CATALOG_DIR, CatalogError, lint_catalog, load_catalog, load_source

app = typer.Typer(
    name="openrx",
    help="OpenRx: public FDA/NIH drug data for AI agents, via sandboxed code execution.",
    no_args_is_help=True,
)
catalog_app = typer.Typer(help="Inspect and check the API catalog.", no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")

CatalogDir = Annotated[
    Path, typer.Option("--dir", help="Catalog folder containing sources/ and examples/.")
]


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
def catalog_lint(directory: CatalogDir = CATALOG_DIR) -> None:
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


@catalog_app.command("capture-examples")
def catalog_capture_examples(
    source_id: Annotated[str, typer.Argument(help="Source id, e.g. openfda.")],
    endpoint: Annotated[
        str | None, typer.Option("--endpoint", help="Only this endpoint id.")
    ] = None,
    directory: CatalogDir = CATALOG_DIR,
) -> None:
    """Call each endpoint's example request on the live API and save a trimmed response."""
    try:
        source = load_source(directory / "sources" / f"{source_id}.yaml")
    except FileNotFoundError:
        typer.echo(f"no source {source_id!r} in {directory / 'sources'}", err=True)
        raise typer.Exit(code=1) from None
    except CatalogError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None

    headers = {"User-Agent": f"openrx/{__version__} (catalog example capture)"}
    with _http_client(headers) as client:
        results = capture_source(source, client, root=directory, only=endpoint)

    for r in results:
        if r.error:
            typer.echo(f"FAILED {source_id}.{r.endpoint_id}: {r.error}", err=True)
        else:
            typer.echo(f"wrote  {r.file}")
    if any(r.error for r in results):
        raise typer.Exit(code=1)


def _http_client(headers: dict[str, str]) -> httpx.Client:
    # A function so tests can swap in a client with a fake transport.
    return httpx.Client(headers=headers, timeout=30)

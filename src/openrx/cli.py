"""Command-line interface: the `openrx` command."""

from pathlib import Path
from typing import Annotated

import httpx
import typer

from openrx import __version__
from openrx.catalog.capture import capture_source
from openrx.catalog.loader import (
    CATALOG_DIR,
    CatalogError,
    lint_catalog,
    load_catalog,
    load_source,
    read_yaml,
)
from openrx.catalog.openapi import (
    OpenAPIImportError,
    Overlay,
    overlay_path,
    spec_path,
    write_source,
)

app = typer.Typer(
    name="openrx",
    help="OpenRx: public FDA/NIH drug data for AI agents, via sandboxed code execution.",
    no_args_is_help=True,
)
catalog_app = typer.Typer(help="Inspect and check the API catalog.", no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")
eval_app = typer.Typer(help="Measure OpenRx (README §6, §12).", no_args_is_help=True)
app.add_typer(eval_app, name="eval")

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
        for warning in r.warnings:
            typer.echo(f"note   {source_id}.{r.endpoint_id}: {warning}", err=True)
        if r.error:
            typer.echo(f"FAILED {source_id}.{r.endpoint_id}: {r.error}", err=True)
        else:
            typer.echo(f"wrote  {r.file}")
    if any(r.error for r in results):
        raise typer.Exit(code=1)


def _http_client(headers: dict[str, str]) -> httpx.Client:
    # A function so tests can swap in a client with a fake transport.
    return httpx.Client(headers=headers, timeout=30)


@catalog_app.command("import-openapi")
def catalog_import_openapi(
    source_id: Annotated[str, typer.Argument(help="Source id, e.g. clinicaltrials.")],
    fetch: Annotated[
        bool, typer.Option("--fetch", help="Download the latest spec first and save it.")
    ] = False,
    directory: CatalogDir = CATALOG_DIR,
) -> None:
    """Regenerate a source's YAML from its OpenAPI spec and hand-written overlay."""
    overlay_file = overlay_path(directory, source_id)
    if not overlay_file.is_file():
        typer.echo(f"no overlay {overlay_file}", err=True)
        raise typer.Exit(code=1)

    if fetch:
        spec_url = Overlay.model_validate(read_yaml(overlay_file)).spec_url
        headers = {"User-Agent": f"openrx/{__version__} (catalog import)"}
        with _http_client(headers) as client:
            response = client.get(spec_url)
        if response.status_code != 200:
            typer.echo(f"fetching {spec_url} failed: HTTP {response.status_code}", err=True)
            raise typer.Exit(code=1)
        # Normalize line endings: the saved copy is committed (LF, .gitattributes).
        text = response.text.replace("\r\n", "\n")
        spec_path(directory, source_id).write_text(text, encoding="utf-8", newline="\n")
        typer.echo(f"saved  {spec_path(directory, source_id)}")

    try:
        out = write_source(directory, source_id)
    except (OpenAPIImportError, FileNotFoundError) as e:
        typer.echo(f"FAILED: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"wrote  {out}")


@app.command("serve")
def serve() -> None:
    """Run the OpenRx MCP server over stdio (for an MCP client or the agent)."""
    from openrx.mcp_server.server import server_from_environment

    server_from_environment().run()


@eval_app.command("footprint")
def eval_footprint() -> None:
    """Tokens the tool definitions cost the model, vs one tool per endpoint (NFR-1)."""
    from openrx.mcp_server.footprint import (
        ENCODING,
        TOKEN_BUDGET,
        per_endpoint_footprint,
        server_footprint,
    )
    from openrx.mcp_server.server import build_server

    class _NoRunners:  # listing tools never runs code
        def run(self, tool: str, code: str) -> dict[str, object]:
            raise AssertionError("not called")

    ours = server_footprint(build_server(_NoRunners()))  # type: ignore[arg-type]
    theirs = per_endpoint_footprint(load_catalog())
    verdict = "within" if ours.total <= TOKEN_BUDGET else "OVER"
    typer.echo(f"tokens ({ENCODING})")
    typer.echo(
        f"  OpenRx        {ours.tool_count:3} tools  {ours.tools:6,} + {ours.instructions} "
        f"instructions = {ours.total:,}  ({verdict} the {TOKEN_BUDGET:,} budget)"
    )
    typer.echo(f"  per endpoint  {theirs.tool_count:3} tools  {theirs.total:6,}")
    typer.echo(f"  ratio         {theirs.total / ours.total:.1f}x")
    if ours.total > TOKEN_BUDGET:
        raise typer.Exit(code=1)

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
eval_app = typer.Typer(help="Measure OpenRx (docs/design.md §6, §12).", no_args_is_help=True)
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


@app.command("ask")
def ask_command(
    question: Annotated[str, typer.Argument(help="A question about drugs.")],
    trace: Annotated[bool, typer.Option("--trace", help="Show every model and tool call.")] = False,
) -> None:
    """Answer a question with the agent, end to end (needs Floci, the Lambdas and Ollama)."""
    import asyncio
    import time

    from openrx.agent.run import ask_question, summarise, timed

    started = time.perf_counter()
    result = asyncio.run(ask_question(question))
    totals = summarise(result, timed(started))

    if trace:
        for event in result.events:
            if event["kind"] == "llm":
                typer.echo(
                    f"  model  {event['ms'] / 1000:6.1f}s  {event['input_tokens']:>6} in "
                    f"{event['output_tokens']:>5} out  {event['tool_calls']} tool call(s)"
                )
            elif event["kind"] == "tool":
                status = "ok" if event["ok"] else f"error {event['error_type']}"
                typer.echo(
                    f"  {event['tool']:<7}{event['ms'] / 1000:6.1f}s  {status}  "
                    f"{event['request_calls']} request(s), {event['bytes_fetched']:,} B fetched, "
                    f"{event['bytes_returned']:,} B returned"
                )
        typer.echo("")
    typer.echo(result.answer)
    typer.echo("")
    reduction = f", data reduction {totals['data_reduction']}x" if totals["data_reduction"] else ""
    typer.echo(
        f"[{totals['seconds']}s: {totals['llm_calls']} model calls ({totals['model_seconds']}s, "
        f"{totals['input_tokens']:,} in / {totals['output_tokens']:,} out tokens), "
        f"{totals['tool_calls']} tool calls ({totals['tool_seconds']}s){reduction}]"
    )


fixtures_app = typer.Typer(help="Move eval fixtures between Floci's S3 and the repo.")
eval_app.add_typer(fixtures_app, name="fixtures")


@fixtures_app.command("export")
def fixtures_export() -> None:
    """Copy recorded fixtures from Floci's S3 into evals/fixtures/ (to commit)."""
    from openrx.evals.deployed import FIXTURE_BUCKET, s3_client
    from openrx.evals.fixtures import FIXTURES_DIR, export_fixtures

    count = export_fixtures(s3_client(), FIXTURE_BUCKET)
    typer.echo(f"exported {count} fixture(s) to {FIXTURES_DIR}")


@fixtures_app.command("import")
def fixtures_import() -> None:
    """Upload evals/fixtures/ into Floci's S3 (after `make up`)."""
    from openrx.evals.deployed import FIXTURE_BUCKET, s3_client
    from openrx.evals.fixtures import import_fixtures

    count = import_fixtures(s3_client(), FIXTURE_BUCKET)
    typer.echo(f"imported {count} fixture(s) into s3://{FIXTURE_BUCKET}")


@eval_app.command("record")
def eval_record() -> None:
    """Run every task's reference, recording the data it needs; save fixtures and answers."""
    from openrx.evals.deployed import (
        FIXTURE_BUCKET,
        lambda_client,
        run_code,
        s3_client,
        set_bridge_mode,
    )
    from openrx.evals.expected import save_expected, shape_problem
    from openrx.evals.fixtures import export_fixtures
    from openrx.evals.tasks import load_tasks

    lam = lambda_client()
    expected: dict[str, object] = {}
    failed = 0
    set_bridge_mode(lam, "replay_or_record")
    try:
        for task in load_tasks():
            result = run_code(lam, task.reference)
            problem = result["error"]["message"][:200] if result.get("error") else None
            problem = problem or shape_problem(task, result.get("result"))
            if problem:
                failed += 1
                typer.echo(f"FAILED {task.id}: {problem}", err=True)
            else:
                expected[task.id] = result["result"]
                typer.echo(f"ok     {task.id} ({result['request_calls']} request(s))")
    finally:
        set_bridge_mode(lam, "live")
    count = export_fixtures(s3_client(), FIXTURE_BUCKET)
    typer.echo(f"exported {count} fixture(s)")
    if failed:
        raise typer.Exit(code=1)
    save_expected(expected)
    typer.echo(f"saved {len(expected)} expected answer(s) to evals/expected.json")


@eval_app.command("run")
def eval_run(
    k: Annotated[int, typer.Option("-k", min=1, help="Attempts per task (pass^k).")] = 3,
    tasks: Annotated[str, typer.Option(help="Comma-separated task ids (default: all).")] = "",
    model: Annotated[str, typer.Option(help="Bedrock model id (OPENRX_MODEL_ID).")] = "",
    resume: Annotated[
        Path | None, typer.Option(help="A runs file to continue: trials in it are skipped.")
    ] = None,
) -> None:
    """Run the agent on the task suite k times and save every trial (docs/design.md §12.2)."""
    import asyncio
    import os

    from openrx.agent.model import DEFAULT_MODEL_ID
    from openrx.agent.run import ask_question
    from openrx.evals.deployed import FIXTURE_BUCKET, lambda_client, s3_client, set_bridge_mode
    from openrx.evals.expected import load_expected
    from openrx.evals.fixtures import list_keys
    from openrx.evals.run import (
        RUNS_DIR,
        append_line,
        pending,
        read_runs,
        run_id,
        run_trial,
        trials,
    )
    from openrx.evals.tasks import load_tasks

    env = dict(os.environ)
    if model:
        env["OPENRX_MODEL_ID"] = model
    model_id = env.get("OPENRX_MODEL_ID", DEFAULT_MODEL_ID)
    suite = load_tasks()
    if tasks:
        wanted = {t.strip() for t in tasks.split(",") if t.strip()}
        unknown = wanted - {t.id for t in suite}
        if unknown:
            typer.echo(f"unknown task(s): {', '.join(sorted(unknown))}", err=True)
            raise typer.Exit(code=2)
        suite = [t for t in suite if t.id in wanted]
    expected = load_expected()
    path = resume or RUNS_DIR / f"{run_id(model_id)}.jsonl"
    done = read_runs(path) if resume else []
    s3, lam = s3_client(), lambda_client()

    async def ask(prompt: str):  # noqa: ANN202 - AgentResult, imported lazily
        return await ask_question(prompt, env)

    async def run_all() -> tuple[int, int, int]:
        passed = recorded = 0
        plan = pending(trials(suite, k), done)
        for n, trial in enumerate(plan, 1):
            record = await run_trial(
                trial, expected[trial.task.id], ask, model_id, lambda: list_keys(s3, FIXTURE_BUCKET)
            )
            append_line(path, record)
            passed += record["passed"]
            recorded += len(record["new_fixtures"])
            mark = "pass" if record["passed"] else ("CRASH" if record["crash"] else "fail")
            typer.echo(
                f"[{n}/{len(plan)}] {mark:<5} {trial.task.id} #{trial.attempt} "
                f"({record['totals']['seconds']}s) {record.get('detail') or ''}".rstrip()
            )
        return passed, len(plan), recorded

    set_bridge_mode(lam, "replay_or_record")
    try:
        passed, total, recorded = asyncio.run(run_all())
    finally:
        set_bridge_mode(lam, "live")
    # Fixtures the agent added stay in Floci's S3 only: the repo keeps the ones the
    # references need (evals/fixtures/), and a fresh setup re-records the rest once.
    typer.echo(f"{passed}/{total} trials passed; {recorded} new fixture(s); saved {path}")


@eval_app.command("report")
def eval_report(
    runs: Annotated[
        Path | None, typer.Option(help="A runs file (default: the latest in results/runs/).")
    ] = None,
) -> None:
    """Compute the docs/design.md §6 metrics from a run; write results/summary.md and a chart."""
    import json

    from openrx.evals.metrics import all_metrics
    from openrx.evals.report import RESULTS_DIR, chart_pass_by_category, failures, render_summary
    from openrx.evals.run import RUNS_DIR, read_runs, rescore
    from openrx.evals.tasks import load_tasks

    if runs is None:
        found = sorted(RUNS_DIR.glob("*.jsonl"))
        if not found:
            typer.echo("no runs yet: run `make eval-run` first", err=True)
            raise typer.Exit(code=1)
        runs = found[-1]
    by_id = {t.id: t for t in load_tasks()}
    records = [rescore(r, by_id[r["task"]]) if r["task"] in by_id else r for r in read_runs(runs)]
    metrics = all_metrics(records)
    k = max(r["attempt"] for r in records)
    source = (
        runs.relative_to(RESULTS_DIR.parent).as_posix()
        if runs.is_relative_to(RESULTS_DIR.parent)
        else runs.name
    )

    summary = render_summary(metrics, source, k)
    (RESULTS_DIR / "summary.md").write_text(summary, encoding="utf-8", newline="\n")
    (RESULTS_DIR / "failures.md").write_text(
        "# Failed trials\n\n" + failures(records) + "\n", encoding="utf-8", newline="\n"
    )
    (RESULTS_DIR / "metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    chart_pass_by_category(metrics, RESULTS_DIR / "pass_by_category.png")
    # Paths, not the summary itself: it has characters (≥, ×) a Windows console can't print.
    typer.echo(
        f"{len(records)} trial(s) from {source}; wrote results/summary.md, "
        "metrics.json, failures.md and pass_by_category.png"
    )

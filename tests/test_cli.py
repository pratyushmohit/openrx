import tomllib
from pathlib import Path

import httpx
import yaml
from samples import OPENAPI_OVERLAY, OPENAPI_SPEC
from typer.testing import CliRunner

from openrx import __version__, cli
from openrx.cli import app

runner = CliRunner()


def test_version_flag_prints_package_version():
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"openrx {__version__}"


def test_no_arguments_shows_help():
    result = runner.invoke(app, [])

    assert "Usage" in result.output
    assert "--version" in result.output


def test_version_matches_pyproject():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    assert __version__ == expected


VALID_SOURCE = """id: openfda
name: openFDA
base_url: https://api.fda.gov
rate_limit: {per_second: 4}
endpoints:
  - id: drug_event
    path: /drug/event.json
    description: Adverse event reports.
    params:
      - {name: search, description: Filter.}
    example_params: {search: 'x:"y" AND z:1'}
"""


def make_catalog(root, source=VALID_SOURCE, with_example=True):
    (root / "sources").mkdir()
    (root / "sources" / "openfda.yaml").write_text(source, encoding="utf-8")
    if with_example:
        example = root / "examples" / "openfda" / "drug_event.json"
        example.parent.mkdir(parents=True)
        example.write_text('{"response": {"results": []}}', encoding="utf-8")


def test_catalog_lint_passes_on_a_valid_catalog(tmp_path):
    make_catalog(tmp_path)

    result = runner.invoke(app, ["catalog", "lint", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert result.output.strip() == "OK: 1 source(s), 1 endpoint(s)"


def test_catalog_lint_lists_problems_and_fails(tmp_path):
    make_catalog(tmp_path, VALID_SOURCE.replace("https://", "http://"))

    result = runner.invoke(app, ["catalog", "lint", "--dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "openfda.yaml: base_url: must be an https URL" in result.output
    assert "FAILED: 1 problem(s) in 1 file(s)" in result.output


def test_capture_examples_writes_files(tmp_path, monkeypatch):
    make_catalog(tmp_path, with_example=False)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [1]}))
    monkeypatch.setattr(cli, "_http_client", lambda headers: httpx.Client(transport=transport))

    result = runner.invoke(app, ["catalog", "capture-examples", "openfda", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "wrote" in result.output
    assert runner.invoke(app, ["catalog", "lint", "--dir", str(tmp_path)]).exit_code == 0


def test_capture_examples_fails_on_upstream_error(tmp_path, monkeypatch):
    make_catalog(tmp_path, with_example=False)
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="down"))
    monkeypatch.setattr(cli, "_http_client", lambda headers: httpx.Client(transport=transport))

    result = runner.invoke(app, ["catalog", "capture-examples", "openfda", "--dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "FAILED openfda.drug_event: HTTP 500" in result.output


def test_capture_examples_unknown_source(tmp_path):
    make_catalog(tmp_path)

    result = runner.invoke(app, ["catalog", "capture-examples", "nope", "--dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "no source 'nope'" in result.output


def test_capture_examples_reports_a_broken_source_file(tmp_path):
    make_catalog(tmp_path, VALID_SOURCE.replace("https://", "http://"))

    result = runner.invoke(app, ["catalog", "capture-examples", "openfda", "--dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "must be an https URL" in result.output


def write_openapi_inputs(root):
    (root / "openapi").mkdir()
    (root / "sources").mkdir(exist_ok=True)
    (root / "openapi" / "trials.yaml").write_text(yaml.safe_dump(OPENAPI_OVERLAY), encoding="utf-8")
    return OPENAPI_SPEC


def test_import_openapi_generates_from_the_saved_spec(tmp_path):
    spec = write_openapi_inputs(tmp_path)
    (tmp_path / "openapi" / "trials.spec.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")

    result = runner.invoke(app, ["catalog", "import-openapi", "trials", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "sources" / "trials.yaml").exists()


def test_import_openapi_fetch_saves_the_spec_with_lf_endings(tmp_path, monkeypatch):
    spec = write_openapi_inputs(tmp_path)
    body = yaml.safe_dump(spec).replace("\n", "\r\n")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    monkeypatch.setattr(cli, "_http_client", lambda headers: httpx.Client(transport=transport))

    args = ["catalog", "import-openapi", "trials", "--fetch", "--dir", str(tmp_path)]
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert b"\r\n" not in (tmp_path / "openapi" / "trials.spec.yaml").read_bytes()


def test_import_openapi_fetch_failure(tmp_path, monkeypatch):
    write_openapi_inputs(tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    monkeypatch.setattr(cli, "_http_client", lambda headers: httpx.Client(transport=transport))

    args = ["catalog", "import-openapi", "trials", "--fetch", "--dir", str(tmp_path)]
    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert "HTTP 503" in result.output


def test_import_openapi_without_saved_spec_fails_cleanly(tmp_path):
    write_openapi_inputs(tmp_path)

    result = runner.invoke(app, ["catalog", "import-openapi", "trials", "--dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_import_openapi_unknown_source(tmp_path):
    result = runner.invoke(app, ["catalog", "import-openapi", "nope", "--dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "no overlay" in result.output

import tomllib
from pathlib import Path

import httpx
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

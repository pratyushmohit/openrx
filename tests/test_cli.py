import tomllib
from pathlib import Path

from typer.testing import CliRunner

from openrx import __version__
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


VALID_SOURCE = """\
id: openfda
name: openFDA
base_url: https://api.fda.gov
rate_limit: {per_second: 4}
endpoints:
  - id: drug_event
    path: /drug/event.json
    description: Adverse event reports.
    example_response: {results: []}
"""


def test_catalog_lint_passes_on_a_valid_catalog(tmp_path):
    (tmp_path / "openfda.yaml").write_text(VALID_SOURCE, encoding="utf-8")

    result = runner.invoke(app, ["catalog", "lint", "--dir", str(tmp_path)])

    assert result.exit_code == 0
    assert result.output.strip() == "OK: 1 source(s), 1 endpoint(s)"


def test_catalog_lint_lists_problems_and_fails(tmp_path):
    broken = VALID_SOURCE.replace("https://", "http://").replace(
        "    example_response: {results: []}\n", ""
    )
    (tmp_path / "openfda.yaml").write_text(broken, encoding="utf-8")

    result = runner.invoke(app, ["catalog", "lint", "--dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "openfda.yaml: base_url: must be an https URL" in result.output
    assert "FAILED: 1 problem(s) in 1 file(s)" in result.output

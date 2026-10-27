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

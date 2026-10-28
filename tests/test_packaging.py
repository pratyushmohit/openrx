import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_wheel_contains_the_catalog_and_examples(tmp_path):
    # D-008: the catalog ships inside the package, so the Lambdas get it for free.
    # Build the real wheel and look inside, rather than trusting the source tree.
    uv = shutil.which("uv")
    assert uv, "uv must be on PATH to build the wheel"
    subprocess.run([uv, "build", "--wheel", "-o", str(tmp_path), "-q"], cwd=ROOT, check=True)

    [wheel] = tmp_path.glob("openrx-*.whl")
    names = set(zipfile.ZipFile(wheel).namelist())

    shipped = {p.name for p in (ROOT / "src/openrx/catalog/sources").glob("*.yaml")}
    assert shipped, "no catalog files in the source tree"
    for name in shipped:
        assert f"openrx/catalog/sources/{name}" in names

    examples = {
        p.relative_to(ROOT / "src").as_posix()
        for p in (ROOT / "src/openrx/catalog/examples").rglob("*.json")
    }
    assert examples, "no example files in the source tree"
    assert examples <= names


def test_lambda_requirements_match_uv_lock():
    # A Lambda pinned to a different version than the one tested here would run
    # code nobody tested.
    import tomllib

    locked = {
        p["name"]: p["version"]
        for p in tomllib.loads((ROOT / "uv.lock").read_text("utf-8"))["package"]
    }
    for requirements in (ROOT / "lambdas").glob("*/requirements.txt"):
        for line in requirements.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if not line:
                continue
            name, _, version = line.partition("==")
            assert version, f"{requirements}: {line!r} must be pinned with =="
            assert locked.get(name.lower()) == version, (
                f"{requirements}: {line} vs uv.lock {locked.get(name.lower())}"
            )

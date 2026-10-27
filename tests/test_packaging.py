import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_wheel_contains_the_catalog_yaml(tmp_path):
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

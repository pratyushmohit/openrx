#!/usr/bin/env bash
# Build a deployment zip for one Lambda: lambdas/<name>/ -> infra/envs/floci/build/<name>.zip
#
# The build runs inside the official AWS Lambda Python image, not on the host:
# - pip then picks the Linux wheels that match the Lambda runtime exactly (our
#   dev machine is Windows; pydantic-monty ships a native `monty` binary);
# - the zip keeps Unix file permissions, so binaries stay executable. A zip
#   made on Windows would drop the executable bit and the binary would not start.
#
# Usage: scripts/build_lambda.sh <lambda-name>
set -euo pipefail

name="${1:?usage: scripts/build_lambda.sh <lambda-name>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
[[ -d "$root/lambdas/$name" ]] || { echo "error: no such Lambda: lambdas/$name" >&2; exit 1; }

image="public.ecr.aws/lambda/python:3.13"

# Git Bash rewrites arguments that look like Unix paths (/src -> C:/Program
# Files/Git/src). Docker needs them untouched.
export MSYS_NO_PATHCONV=1
# On Windows, give Docker a C:/... path; elsewhere `pwd -W` fails and we keep $root.
host_root="$(cd "$root" && pwd -W 2>/dev/null || echo "$root")"

docker run --rm --entrypoint "" \
  -v "$host_root:/src" -w /src \
  -e NAME="$name" \
  "$image" bash -euo pipefail -c '
    pkg=/tmp/pkg
    if [[ -f "lambdas/$NAME/requirements.txt" ]]; then
      # --only-binary: use prebuilt wheels, never compile from source.
      pip install --quiet --no-cache-dir --only-binary=:all: \
        -r "lambdas/$NAME/requirements.txt" --target "$pkg"
    fi
    mkdir -p "$pkg"
    cp lambdas/$NAME/*.py "$pkg"/

    # No zip CLI in the image; Python zipfile records each file mode.
    mkdir -p infra/envs/floci/build
    python3 - "$pkg" "infra/envs/floci/build/$NAME.zip" <<PY
import os, sys, zipfile
src, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for dirpath, _, files in os.walk(src):
        for f in sorted(files):
            path = os.path.join(dirpath, f)
            z.write(path, os.path.relpath(path, src))
print(f"built {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
PY
  '

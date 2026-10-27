# Decisions

One entry per non-obvious choice: what we decided, what else we considered, and why.
Newest at the bottom. Don't edit old entries; if a decision changes, add a new entry
that supersedes it.

---

## D-001: Python 3.13

**Date:** 2026-10-27 · **Status:** Accepted

**Context:** Lambda code must run on a Lambda runtime that Floci supports. The first
draft assumed 3.12 to be safe.

**Decision:** Python 3.13 everywhere (local and Lambda).

**Why:** Floci supports any runtime with an official AWS Lambda container image,
including `python3.13`. One version for local dev and Lambda avoids "works on my
machine" differences, especially for compiled wheels like `pydantic-monty`.

---

## D-002: `fastmcp` instead of the official `mcp` SDK

**Date:** 2026-10-27 · **Status:** Accepted

**Context:** We need a Python MCP server exposing two tools over stdio.

**Options:**
1. Official `mcp` SDK, using its built-in FastMCP layer.
2. Standalone `fastmcp` package, which builds on top of `mcp`.

**Decision:** `fastmcp`.

**Why:** It covers what the official SDK does and adds more on top (for example, an
in-memory test client for calling tools without spawning a process). The cost is one
extra dependency on a third-party project. It still uses the official `mcp` package
underneath, so the wire protocol is the same.

---

## D-003: Floci starts clean on every `make up`

**Date:** 2026-10-27 · **Status:** Accepted

**Context:** Terraform records what it created in a state file. Floci can either keep
its emulated resources on disk across restarts or hold them in memory only. If the two
disagree (e.g. Floci's data was wiped but Terraform's state wasn't), Terraform plans
against resources that no longer exist.

**Options:**
1. Persist Floci data (`./data` volume). Faster restarts; wiping one side means
   remembering to wipe the other.
2. In-memory only; `make up` recreates the container and deletes local Terraform state,
   then `make tf-apply` rebuilds everything.

**Decision:** Option 2.

**Why:** Every session starts from a known-empty account, with no leftovers hiding bugs,
and it continuously proves that the infra can be built from scratch (README NFR-6).
Applying on Floci takes seconds, so nothing is lost. Note: the Floci image declares
`/app/data` as a volume, so `make up` passes `--renew-anon-volumes`; without it compose
reuses the old container's volume on recreate.

---

## D-004: Give Floci the Docker socket to run Lambdas

**Date:** 2026-10-27 · **Status:** Accepted

**Context:** Floci runs each Lambda in a real Docker container, which it starts itself.
That requires access to Docker's control socket (`/var/run/docker.sock`).

**Options:**
1. Mount the Docker socket into the Floci container (Floci's default executor).
2. Floci's Kubernetes executor (each Lambda as a pod).
3. Run Monty in-process, without Lambda.

**Decision:** Option 1, plus a fixed Docker network named `openrx` that Floci attaches
Lambda containers to (`FLOCI_SERVICES_LAMBDA_DOCKER_NETWORK`), and `FLOCI_HOSTNAME=floci`
so those containers can call back to Floci.

**Why:** Simplest setup that keeps real per-invocation container isolation. The cost:
anything holding the socket effectively controls Docker on this machine. Acceptable for
a local emulator we run ourselves from a pinned image; not something to copy to shared
or production hosts. Kubernetes would be heavy for a laptop; in-process loses the
process boundary that the design relies on (README §7.2, layer 2).

---

## D-005: Model-written code runs only in Lambda

**Date:** 2026-10-27 · **Status:** Accepted

**Context:** Monty 1.4 runs code in its own pool of worker processes, with crash
recovery and time/memory/recursion limits. That raised the question of whether Lambda
is still needed, or whether to offer two runner backends (Monty on the host for tests
and fast development, Lambda for deployment).

**Options:**
1. Lambda only.
2. Runner interface with a host backend and a Lambda backend.
3. Host only; drop Lambda.

**Decision:** Option 1. There is no code path that runs model-written code on the host,
including in tests.

**Why:** Monty's workers protect against buggy code, but not against a bug in Monty
itself; its docs say a worker "can never be made fully crash-proof" against adversarial
input. A host worker runs as the user, with the user's files, environment variables and
network. Inside Lambda, an escape lands in a throwaway container whose only AWS
permission is invoking the bridge. A host backend "just for tests" is still a path where
model code runs on the host, so it is left out entirely.

**Cost:** anything that executes sandboxed code (experiments, tests, evals) needs Floci
running and pays Lambda's overhead (measured in E2: 274 ms cold, ~48 ms warm p50).

---

## D-006: Build Lambda zips inside the AWS Lambda image

**Date:** 2026-10-27 · **Status:** Accepted

**Context:** The runner Lambdas need `pydantic-monty`, which ships a native Linux
binary (`monty`). We develop on Windows.

**Options:**
1. `pip install --platform manylinux_2_28_x86_64 --only-binary=:all:` on the host, zip
   on the host (Terraform `archive_file`).
2. Build inside `public.ecr.aws/lambda/python:3.13` with Docker
   (`scripts/build_lambda.sh`, `make build LAMBDA=<name>`).
3. Container-image Lambdas instead of zips.

**Decision:** Option 2.

**Why:** pip runs on the exact OS, glibc and Python as the Lambda, so it picks the right
wheels without platform flags. The zip is written on Linux, so the binary keeps its
executable bit; zips made on Windows drop it and the worker would fail to start. This is
what AWS's own tooling does (`sam build --use-container`). Container images would also
work but add an image registry to the setup; zips are enough at 18.8 MB.

**Cost:** builds need Docker and take ~40 s (pip download). The image is referenced by
tag `python:3.13`, not digest; pin the digest if builds ever differ between machines.

---

## D-007: Run Floci with IAM enforcement on; reference secrets by ARN

**Date:** 2026-10-27 · **Status:** Accepted

**Context:** By default Floci allows every call regardless of IAM policies. E5 showed that
with `FLOCI_SERVICES_IAM_ENFORCEMENT_ENABLED=true` it denies what a role does not allow.
It also showed a Floci quirk: a permitted secret read is denied when the secret is
requested by name instead of by full ARN.

**Decision:**
1. Enforcement is always on (`docker-compose.yml`), not an opt-in toggle.
2. Code and configuration refer to secrets by their full ARN.

**Why:** With enforcement off, a missing or wrong permission would only fail on real
AWS, the most expensive place to find it, and the least-privilege design (README §7.2,
layer 3) would never be tested locally. Referencing secrets by ARN avoids the Floci quirk
and is unambiguous on real AWS too.

**Cost:** IAM mistakes fail locally instead of silently working, which is the point.
Floci's known enforcement gaps mean "allowed on Floci" still isn't proof of
"denied on AWS"; the real-AWS check stays in FR-21.

---

## D-008: `src` layout, with the catalog YAML inside the package

**Date:** 2026-10-27 · **Status:** Accepted

**Context:** The first application code (M1) needs a home, and the catalog YAML files
must reach the runner and bridge Lambdas.

**Options:**
1. Flat layout: `openrx/` at the repo root; catalog in a top-level `catalog/`.
2. `src` layout: `src/openrx/`; catalog in `src/openrx/catalog/sources/`.

**Decision:** Option 2, built with uv's `uv_build` backend.

**Why:**
- With a flat layout Python imports `openrx/` straight from the working directory, so
  tests can pass even when packaging is broken (missing files, missing package data).
  With `src/`, code is importable only once installed, so those mistakes show up at
  once.
- The repo holds a lot that is not package code (`infra/`, `lambdas/`, `docs/`,
  `scripts/`, eval data); `src/` makes the boundary explicit.
- Lambda builds can `pip install` the package and get exactly the packaged code. With
  the YAML as package data, the catalog travels with it; no separate copy step to forget.

**Cost:** one extra folder level, and editing the catalog means going into
`src/openrx/catalog/sources/` rather than a top-level folder.

---

## D-009: Example responses live in their own generated files

**Date:** 2026-10-27 · **Status:** Accepted

**Context:** Every catalog endpoint needs a real, trimmed example response (M1 exit
criterion), captured from the live API by a command. Writing them into the
hand-written YAML would mean a tool rewriting files people edit.

**Options:**
1. Examples inside each source's YAML, rewritten with `ruamel.yaml` to keep comments,
   anchors and layout.
2. One generated JSON file per endpoint: `examples/<source>/<endpoint>.json`.

**Decision:** Option 2. YAML may not contain `example_response`; the loader attaches it
from the example file, so the rest of the code still sees `endpoint.example_response`.
Each endpoint declares `example_params`, the exact request that produced its example,
and each example file records that request and the capture date.

**Why:** Hand-written and generated content don't share a file. The tool never touches
YAML that people wrote; refreshing examples shows up as changes to example files only,
so config changes stay small and visible in review; examples can be deleted and
regenerated freely; no extra dependency. The same reason generated lockfiles are kept
apart from what people write.

**Cost:** a source is now one YAML plus one JSON per endpoint. `catalog lint` covers the
gap: it requires an example for every endpoint and flags example files whose endpoint or
source no longer exists.

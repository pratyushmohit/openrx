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

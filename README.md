# OpenRx — an MCP server with sandboxed code execution (search + execute) for public drug data

| | |
|---|---|
| **Author** | Pratyush Mohit |
| **Status** | Draft v0.3 |
| **Last updated** | 2026-10-27 |
| **Repo** | `pratyushmohit/openrx` |
| **Names** | Project/package/CLI/MCP server: `openrx`. MCP tools: `search`, `execute` (clients typically show them as `openrx__search`, `openrx__execute`). |

> **For Claude Code:** read this whole doc, then start with: "Propose an implementation plan for Milestone 0. Don't write code until I approve the plan." This is a learning project: for each non-obvious choice, explain *why* in the commit message or `docs/decisions.md`. Prefer readable code over clever code.

---

## 1. TL;DR

OpenRx is an MCP server that gives any AI agent access to five free public FDA/NIH drug-data APIs through **two tools**. With `search`, the agent writes Python to explore an API catalog. With `execute`, it writes Python that calls the APIs, loops, joins, and filters. The code runs in **Pydantic Monty** (a deny-by-default Python interpreter in Rust) **inside AWS Lambda on Floci** (a local AWS emulator). API keys never enter the sandbox. Only the final result goes back to the model, so the tool footprint stays at about 1.5k tokens however many endpoints exist, and raw API data stays out of the model's context.

## 2. Background and problem

- **Agents:** MCP servers usually expose one tool per endpoint. Each tool definition costs context tokens. Many tools make models worse at picking the right one, and chaining calls forces every intermediate result through the model, which wastes tokens, time, and accuracy.
- **Users:** pharmacovigilance, medical-affairs, and regulatory analysts answer cross-source questions ("is there a safety signal for drug X?") by hand across openFDA, ClinicalTrials.gov, PubMed, and DailyMed in separate tabs and spreadsheets.
- **Prior art:** Cloudflare ("Code Mode", Sep 2025; search-and-execute MCP server, Feb 2026) and Anthropic ("Code execution with MCP") showed that letting models write code against APIs beats direct tool calling. OpenRx re-implements the idea in Python on AWS primitives, using a language-level sandbox, and measures it on a real domain. Credit these in the README.

## 3. Goals and non-goals

### Goals
- **G1 (product):** any MCP client can answer cross-source drug-safety and clinical-evidence questions through OpenRx.
- **G2 (efficiency):** the tool footprint stays roughly constant as the API surface grows, and most fetched data never reaches the model.
- **G3 (security):** model-written code has no network, filesystem, or credential access. Its only capabilities are the functions OpenRx provides.
- **G4 (evidence):** a reproducible eval suite with published quality, efficiency, latency, and security numbers.
- **G5 (learning):** MCP, LangGraph, Lambda/IAM/Secrets Manager, Terraform, local LLM serving, SLO-driven engineering.

### Non-goals
- One MCP tool per endpoint.
- A UI beyond the CLI (v1).
- Real patient data or employer/proprietary data. Outputs are research/demo only, not medical advice.
- A real AWS account in v1. Code must still be real-AWS-compatible.
- A direct tool-calling baseline agent in v1 (P2 stretch).

## 4. Users and critical user journeys (CUJs)

| ID | User | Journey | Success looks like |
|---|---|---|---|
| CUJ-1 | Analyst via an agent | "What are the top reported adverse events for drug X, and which are on its label?" | Correct, sourced answer in one conversation, under the latency SLO |
| CUJ-2 | Analyst via an agent | "For all Phase 3 trials of drug class Y, list sponsors, status, and recent recalls of the drugs." | Fan-out across sources done in a few `execute` calls, not dozens of turns |
| CUJ-3 | Developer | Connects OpenRx to Claude Desktop or another MCP client with no prior knowledge of it | Agent understands the server from its name, `instructions`, and tool descriptions alone |
| CUJ-4 | Maintainer | Adds a new endpoint or a new source | Edit catalog files only. No new tools, no agent changes. |

## 5. Requirements

Priorities: **P0** = must have for v1 (no launch without it), **P1** = should have for v1, **P2** = nice to have / later.

### 5.1 Functional requirements

| ID | Pri | Requirement |
|---|---|---|
| FR-1 | P0 | MCP server exposes exactly two tools, `search(code)` and `execute(code)`, over stdio. |
| FR-2 | P0 | `search` runs model code with read-only catalog functions: `catalog_sources()`, `catalog_endpoints(source=None)`, `catalog_endpoint(source, id)`, `catalog_find(text)`, `catalog_guide(source)`. No `request()`. |
| FR-3 | P0 | `execute` runs model code with `request(source, path, params=None)` (async) plus the read-only catalog functions. |
| FR-4 | P0 | Code convention: Monty-compatible Python; the value of the last expression is the result; `print()` output captured separately. |
| FR-5 | P0 | Catalog covers openFDA, ClinicalTrials.gov, PubMed (E-utilities), RxNorm, DailyMed in one unified schema (§8.3). ClinicalTrials.gov imported from its published OpenAPI spec. |
| FR-6 | P0 | `request()` only allows `source` + `path` pairs present in the catalog. Rejects absolute URLs, path traversal, and unknown sources with errors the model can act on. |
| FR-7 | P0 | Bridge injects API keys from Secrets Manager. The sandbox never sees them. |
| FR-8 | P0 | Results truncated to a cap (default ~10k tokens) with an explicit `truncated: true` marker and a hint to filter in code. |
| FR-9 | P0 | Errors (type errors, runtime exceptions with traceback, budget/timeout exceeded, upstream errors) returned in a structured form so the model can fix and retry. |
| FR-10 | P0 | Server sends an MCP `instructions` string and a human-readable title ("OpenRx — public FDA/NIH drug data"), counted in the token budget. |
| FR-11 | P0 | Record/replay of upstream HTTP responses (`record`, `replay`, `live` modes) for reproducible evals. |
| FR-12 | P0 | Reference LangGraph agent with local Ollama; `openrx ask "<question>"` works end to end. |
| FR-13 | P0 | Eval harness and report (§6, §12). |
| FR-14 | P1 | Pre-execution type check with Monty's bundled `ty` against generated stubs; type errors returned without running. Toggleable. |
| FR-15 | P1 | Per-source rate limiting and a per-execution `request()` budget (default 50 calls). |
| FR-16 | P1 | `openrx catalog lint` validates catalog files. `openrx catalog import-openapi` converts OpenAPI specs. |
| FR-17 | P1 | Works from at least one third-party MCP client (e.g. Claude Desktop), documented in the README. |
| FR-18 | P2 | Streamable HTTP transport, deployed on Floci (API Gateway + Lambda). |
| FR-19 | P2 | Alternative sandbox backends behind the same runner interface (Docker `--network none`, Daytona/E2B) with a comparison. |
| FR-20 | P2 | Direct tool-calling baseline agent for an A/B comparison. |
| FR-21 | P2 | Deploy to real AWS (free tier) with `real_aws=true` and re-run security evals there. |

### 5.2 Non-functional requirements

| ID | Pri | Requirement |
|---|---|---|
| NFR-1 | P0 | Tool-definition footprint (both tool descriptions + `instructions`) ≤ **1,500 tokens**. Enforced by a test. |
| NFR-2 | P0 | Sandbox escape suite (§6.5) passes **100%**. Enforced by a test in CI. |
| NFR-3 | P0 | No secrets in the repo, sandbox, logs, or eval outputs. |
| NFR-4 | P0 | Latency SLOs in §6.4 met on the reference machine (calibrated after M0 in `docs/slos.md`). |
| NFR-5 | P0 | Eval runs reproducible: same fixtures + same model + temperature 0 → success rate varies ≤ ±5 pts across runs. |
| NFR-6 | P1 | Infra fully defined in Terraform. `make up && make deploy` from a clean checkout works. |
| NFR-7 | P1 | Structured logs + per-call metrics for every tool call and LLM call (§10). |
| NFR-8 | P1 | Unit-test coverage ≥ 80% for `catalog`, `runner`, `bridge`. |
| NFR-9 | P1 | `ruff` clean, fully type-hinted, CI green on every push. |

## 6. Success metrics and evals

Targets marked **(provisional)** get re-set from the M0/M4 baseline. Setting targets from measured data, not guesses, is standard SLO practice. Record the final numbers and the reasoning in `docs/slos.md`.

### 6.1 Quality metrics (is the answer right?)

| Metric | Definition | Target |
|---|---|---|
| **Task success rate (pass@1)** | % of tasks where the first run's answer passes the scorer | ≥ 70% on the medium local model **(provisional)** |
| **pass^k (consistency)** | % of tasks passed on **all** k=3 runs. Measures reliability, not luck. | ≥ 55% **(provisional)** |
| **Partial score** | Mean per-task score in [0,1] (e.g. 4 of 5 correct items = 0.8) | Report |
| **Grounding rate** | % of factual claims (numbers, names, IDs) in the final answer that appear in data returned by `execute`. Deterministic check for numbers/IDs; LLM judge for prose. | ≥ 95% |
| **Hallucinated-endpoint rate** | % of `request()` calls rejected because the source/path isn't in the catalog | ≤ 5% |
| **Search hit rate** | % of tasks where the right endpoint is found within ≤ 2 `search` calls | ≥ 80% **(provisional)** |
| **First-try code success** | % of `execute` calls that run without type/runtime error | Report; track over prompt iterations |
| **Self-repair rate** | % of failed `execute` calls followed by a successful retry within the same task | ≥ 60% **(provisional)** |

All quality metrics are broken down by task category (§12.1) and by model.

### 6.2 Efficiency metrics (what does it cost?)

| Metric | Definition | Target |
|---|---|---|
| **Tool footprint** | Tokens for both tool definitions + `instructions` | ≤ 1,500 (P0) |
| **Footprint vs per-endpoint baseline** | Footprint compared with auto-generated one-tool-per-endpoint schemas from the catalog (static, no agent run) | Report; expect ≥ 10× smaller |
| **Tokens per task** | Input + output tokens across all LLM calls in a task | Report p50/p95 by category |
| **Data reduction ratio** | Bytes fetched by the sandbox ÷ bytes returned to the model | Report; expect ≫ 1 on fan-out tasks |
| **LLM turns per task** | Number of model calls to reach an answer | Report; expect ≤ 6 p95 |
| **Requests per execute** | `request()` calls per `execute` call | Report |

### 6.3 Reliability metrics (does it break?)

| Metric | Definition | Target |
|---|---|---|
| **Infra error rate** | % of tool calls failing for reasons *not* caused by model code (Lambda errors, bridge crashes, serialisation bugs) | < 1% |
| **Timeout rate** | % of `execute` calls hitting the wall-clock limit | < 3% |
| **Upstream error rate** | % of `request()` calls with 4xx/5xx from public APIs (live mode) | Report; retries keep task impact < 2% |

Separating "model's fault" from "our fault" is the key idea: only the infra error rate is an SLO OpenRx owns.

### 6.4 Latency SLOs

Latency is reported as **percentiles**, not averages: **p50** (median, the typical case), **p95** (1 in 20 is slower), **p99** (1 in 100 is slower, the tail users complain about). Averages hide the tail.

Measured on the reference machine (document its CPU/RAM/GPU), in **replay mode**, so upstream API latency doesn't pollute our numbers. Live-mode latency is reported separately.

| Path (SLI) | p50 | p95 | p99 |
|---|---|---|---|
| `search` call end to end (MCP in → result out), warm | ≤ 300 ms | ≤ 800 ms | ≤ 1.5 s |
| `execute` call, warm, ≤ 5 requests | ≤ 1 s | ≤ 2.5 s | ≤ 5 s |
| Sandbox overhead only (Lambda invoke + Monty start + teardown, excluding code work) | ≤ 100 ms | ≤ 250 ms | ≤ 500 ms |
| Bridge `request()` overhead (excluding upstream time) | ≤ 30 ms | ≤ 80 ms | ≤ 150 ms |
| Lambda cold start (first call after deploy/idle) | Report | Report | Report |
| Full task end to end with local model | Report | ≤ 90 s **(provisional)** | Report |

After M0, sandbox and bridge overhead targets are confirmed and the others stay provisional; `docs/slos.md` is the source of truth, with the measured baselines. For full tasks, report model time and tool time separately, so it's clear whether the model or OpenRx is slow.

### 6.5 Security evals

| Eval | Pass criteria | Pri |
|---|---|---|
| **Escape suite:** from `execute` code, attempt `import socket/urllib/subprocess/os`, `open()`, env-var reads, `__import__`, introspection escapes (`().__class__.__subclasses__()` etc.), `eval`/`exec` | 100% blocked with clean errors, host unaffected | P0 |
| **Resource abuse:** infinite loop, memory bomb, deep recursion, huge output | Killed by limits within the configured bounds, MCP server stays healthy | P0 |
| **Request abuse:** non-catalogued paths, absolute URLs, `../` traversal, budget exhaustion | 100% rejected | P0 |
| **Secret exposure:** search all sandbox-visible state, all tool outputs, and all logs for key values | 0 occurrences | P0 |
| **AWS layer checks:** runner calls Secrets Manager directly; runner invokes a Lambda other than the bridge | Report as *observed on Floci* vs *by design on real AWS* | P1 |
| **Prompt injection via data:** fixtures containing planted instructions inside API text (e.g. a fake label field saying "ignore previous instructions and…") | Agent does not follow planted instructions in ≥ 95% of injected tasks | P1 |

## 7. Architecture overview

Diagrams of the components and trust zones, the path of one `execute` call, and build vs run time: [`docs/architecture.md`](docs/architecture.md).

```
 MCP client (LangGraph agent + Ollama, Claude Desktop, ...)
        │  sees: server "openrx", instructions, 2 tools
        ▼
 ┌─────────────────── OpenRx MCP server (host, stdio) ───────────────────┐
 │  search(code)  ──invoke──►  search-runner Lambda                      │
 │  execute(code) ──invoke──►  execute-runner Lambda                     │
 └───────────────────────────────────────────────────────────────────────┘
                     │                              │
 ┌──────────────── Floci (AWS emulator :4566) ────────────────────────────┐
 │ search-runner Lambda           execute-runner Lambda                   │
 │  ┌ Monty ┐ catalog_* fns        ┌ Monty ┐ request() ──► invoke bridge │
 │  - catalog bundled in           - catalog_* fns (read-only)            │
 │  - NO AWS permissions           - IAM: lambda:Invoke on bridge ONLY    │
 │                                          ▼                             │
 │                                 bridge Lambda                          │
 │                                  - validate source/path vs catalog     │
 │                                  - host allowlist, rate limits, budget │
 │                                  - inject keys from Secrets Manager    │
 │                                  - record/replay fixtures              │
 │                                  - call public APIs                    │
 │ Secrets Manager: API keys (readable by bridge role only)               │
 └────────────────────────────────────────────────────────────────────────┘
```

### 7.1 Concept mapping

| Cloudflare | OpenRx |
|---|---|
| V8 isolate per snippet | Fresh Monty interpreter per snippet, inside a Lambda invocation |
| Bindings instead of network access | Monty external functions (`request`, `catalog_*`), the code's only capabilities. `request` is backed by `lambda:InvokeFunction` on the bridge, the runner's only IAM permission. |
| Host Worker holds credentials | Bridge Lambda reads Secrets Manager; runner roles can't |
| OpenAPI spec stays server-side | Catalog lives in the runner; only query results enter context |

### 7.2 Defence in depth

1. **Monty (language level; enforced everywhere, including Floci):** no filesystem, network, env vars, subprocesses, or side-effecting imports. Only our external functions exist. Per-run limits on time, memory, allocations, and recursion depth.
2. **Lambda (process level):** each run in its own invocation and container, so a sandbox crash can't take down the MCP server, and an escape from Monty itself lands in a throwaway container with no host files, no secrets, and only the permission to invoke the bridge. Lambda timeout and memory as outer limits. Monty also runs code in its own worker processes, but those would run as the host user; Lambda is what contains an interpreter escape. Model code runs **only** in Lambda, never on the host (D-005).
3. **IAM + Secrets Manager (credential level):** only the bridge role can read secrets.
4. **VPC (network level; real AWS only):** runners in private subnets with no NAT/IGW, only a Lambda VPC endpoint. Defined in Terraform behind `real_aws`.

**Honest limitation:** Floci emulates AWS, but not every security control. Measured in M0 (`docs/m0-findings.md`, E5), with Floci's IAM enforcement turned on (D-007):

| Layer | Guarantee | On Floci (observed) | On real AWS |
|---|---|---|---|
| 1. Monty | Model code has no network, files, env vars or imports beyond our functions | Enforced | Enforced |
| 2. Lambda | Each run in its own container, separate from the MCP server | Enforced (real Docker containers) | Enforced |
| 3. IAM | Runner may only invoke the bridge; only the bridge may read the secret | Enforced (forbidden calls get `AccessDeniedException`), with known Floci gaps (some actions unchecked, resource policies not evaluated) | Enforced, by design |
| 4. VPC | Runner containers cannot reach the internet | **Not enforced:** Lambda containers have normal internet access | Enforced, by design (private subnet, no NAT) |

So locally, an escape from Monty (layer 1) would still be contained by layers 2–3, but not 4.

## 8. Detailed design

### 8.1 Tech stack

| Concern | Choice |
|---|---|
| Language | Python 3.13 (matches the `python3.13` Lambda runtime, which Floci supports) |
| Package manager | `uv` |
| MCP server | `fastmcp` (standalone FastMCP, built on the official `mcp` SDK), stdio |
| Code sandbox | Pydantic Monty (`pydantic-monty`), inside the runner Lambdas |
| Agent | LangGraph (hand-built graph) + `langchain-mcp-adapters` |
| LLM | Local Ollama via `langchain-ollama` (`ChatOllama`); model from config |
| AWS emulation | Floci (`floci/floci`, `http://127.0.0.1:4566`; not `localhost`, which adds ~43 ms per request on Windows, see E4) |
| AWS SDK | `boto3`, `endpoint_url` from `AWS_ENDPOINT_URL`, dummy creds |
| Infra-as-code | Terraform, `hashicorp/aws` provider with endpoints overridden to Floci, dummy creds, `skip_credentials_validation` / `skip_requesting_account_id` / `skip_metadata_api_check`. Modules shared between `envs/floci` and `envs/aws`. Local state for v1. Lambda zips built in the Makefile, including the Monty wheel for the right architecture. |
| HTTP (bridge) | `httpx`, retries with backoff, timeouts |
| Token counting | One tokenizer (e.g. `tiktoken` or the Ollama model's), documented |
| Tests / quality | `pytest`, `pytest-asyncio`, `ruff`, `mypy` or `pyright` |
| Eval data | Parquet + `matplotlib` |
| Local orchestration | `docker compose` (Floci, optionally Ollama) + `Makefile` |

### 8.2 Data sources (all free)

| Source id | Base URL | Auth |
|---|---|---|
| `openfda` | `https://api.fda.gov` | Optional `api_key` query param |
| `clinicaltrials` | `https://clinicaltrials.gov/api/v2` | None |
| `pubmed` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils` | Optional `api_key`; send `tool` and `email` |
| `rxnorm` | `https://rxnav.nlm.nih.gov/REST` | None |
| `dailymed` | `https://dailymed.nlm.nih.gov/dailymed/services/v2` | None |

Notes: NLM retired the RxNav drug-interaction API in 2024. Use the `drug_interactions` field of openFDA labels instead. Rate limits differ per source (e.g. NCBI 3 req/s without a key, 10 with). Verify every endpoint against current official docs.

### 8.3 Catalog

One hand-written YAML file per source in `src/openrx/catalog/sources/`, validated by `schema.py`, plus one generated example response per endpoint in `src/openrx/catalog/examples/<source>/<endpoint>.json` (D-009):

```yaml
id: openfda
name: openFDA
base_url: https://api.fda.gov
auth: {type: query_param, name: api_key, secret: openrx/openfda_api_key, optional: true}
rate_limit: {per_second: 4}
guide: |   # query syntax for the model: field:"value" AND ..., count, limit/skip
endpoints:
  - id: drug_event
    path: /drug/event.json
    method: GET            # the only method allowed
    description: FAERS adverse event reports
    params: [{name: search, type: string, description: ..., example: ...}]
    example_params: {search: 'patient.drug.openfda.generic_name:"metformin"', limit: 2}
    response_notes: key fields and shapes
```

`openrx catalog capture-examples <source>` sends each endpoint's `example_params` request to the live API and writes the trimmed response to its example file; `openrx catalog lint` checks the YAML, requires an example for every endpoint, and flags leftover example files.

- The catalog is the single source of truth: catalog functions read it, and the bridge validates against it.
- `catalog_find()` ranks endpoints by keyword matches weighted by field and by how rare a word is, so endpoint descriptions should use the words people search with ("trials", not only "studies"). A small search-quality test (`tests/catalog/test_functions.py`) fails if an edit makes search worse.
- `catalog_endpoint()` returns a shortened example by default (one item per list, short strings, every field name kept); pass `full_example=True` for the stored one.
- ClinicalTrials.gov is generated by `openrx catalog import-openapi clinicaltrials` from a saved copy of its OpenAPI spec plus a hand-written overlay in `src/openrx/catalog/openapi_specs/` (D-011). The others are hand-written.

### 8.4 MCP tools

- **Descriptions are the model's only documentation.** Each covers: the script convention, the supported Python subset (short), the external function signatures, a ~5-line example, and the list of source ids.
- **`instructions`** (sent at initialisation), e.g.: *"Public FDA and NIH drug data (openFDA, ClinicalTrials.gov, PubMed, RxNorm, DailyMed). Use `search` to find endpoints and query syntax, then `execute` to run Python that fetches and processes data. Code runs in a sandbox; only the final result is returned. Research use only, not medical advice."*
- **Return shape** for both tools: `{result, stdout, error, traceback, type_errors, truncated, duration_ms, request_calls, bytes_fetched, bytes_returned}`.
- **Limits** per `execute`: 50 `request()` calls, 60 s wall clock, per-response byte cap. All configurable.

### 8.5 Runner Lambdas

- `search-runner`: catalog bundled in; no AWS permissions.
- `execute-runner`: IAM allows only `lambda:InvokeFunction` on the bridge.
- Shared runner library, executed only inside the runner Lambdas (D-005): optional `ty` type check → fresh Monty interpreter with resource limits → external-function dispatch (async functions or start/resume; decide after M0) → capture stdout → serialise → truncate → return.
- Package `pydantic-monty` for the Floci Lambda image's OS/architecture.

### 8.6 Bridge Lambda

Input `{source, path, params, execution_id}`. It validates against the catalog, enforces the host allowlist and per-source rate limits, decrements the per-execution budget, injects auth from Secrets Manager (cached per container), applies record/replay (`fixtures/<source>/<hash>.json`; how fixtures reach the Lambda is an open question), and calls upstream with timeouts and backoff on 429/5xx. Returns `{status, data, bytes, truncated}`.

### 8.7 Reference agent

Hand-built LangGraph graph → MCP via `langchain-mcp-adapters` → `ChatOllama`, temperature 0, `max_steps` 15. A short system prompt (search first, then execute, return focused results); all API knowledge comes from the tools. Callbacks log tokens (`usage_metadata`), latency, and tool calls.

## 9. Alternatives considered

| Decision | Chosen | Alternatives | Why |
|---|---|---|---|
| Tool design | 2 tools (search + execute) | One tool per endpoint; single `code` tool with the full typed API in its description | Footprint stays constant as the catalog grows; the full API in one description still bloats context |
| Sandbox | Monty inside Lambda | Restricted CPython (`exec` with stripped builtins); Docker `--network none`; Pyodide/WASM; Daytona/E2B/Modal | Restricted CPython is notoriously escapable. Docker is heavier and slower to start. Pyodide is slow to start. Managed sandboxes cost money, need egress, and default to "allow everything". Monty is deny-by-default with microsecond startup; Lambda adds process isolation plus AWS learning. Trade-off: Monty supports only a Python subset. |
| Execution host | Lambda on Floci, only (no local backend) | In-process in the MCP server; Monty's worker processes on the host; both behind a runner interface | Monty's workers already survive crashes, but they run as the host user with its files, env and network. Lambda contains an interpreter escape and gives the IAM/Secrets story. A local backend for tests would mean model code can run on the host, so there is none (D-005). |
| Credentials | Separate bridge Lambda | Keys passed into the runner | Keeps credentials out of the sandbox by design |
| Agent framework | LangGraph (hand-built) | `create_react_agent`, PydanticAI, OpenAI Agents SDK, raw loop | Industry-relevant skill; a hand-built graph gives precise metric hooks |
| LLM | Local Ollama | Free-tier cloud APIs, paid frontier APIs | Zero cost; also tests whether the pattern helps small models |
| IaC | Terraform | OpenTofu, CDK, SAM | Most widely used; the same code targets Floci and real AWS |

## 10. Observability

- **Structured JSON logs** with a `trace_id` per task and a `span_id` per LLM call, tool call, and `request()`. Never log secrets or full upstream payloads (log sizes and hashes instead).
- **Metrics per span:** duration, tokens, bytes fetched/returned, error class (`model_code` | `infra` | `upstream` | `limit`).
- **The eval report computes every metric in §6 from these logs**, so the same instrumentation serves debugging and evaluation.
- P2: export traces in OpenTelemetry format.

## 11. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Monty's Python subset is too limited for the model's typical code | Med | High | Feature check moved from M0 to M3 (runner build); describe the subset in tool descriptions; add helper external functions (e.g. `parse_date`) for gaps |
| ~~`pydantic-monty` doesn't run in the Floci Lambda image (arch/glibc)~~ | Retired | — | M0 E3a: runs, ~30 ms added to a cold start. Zips built inside the Lambda image (D-006) |
| ~~Floci doesn't support Lambda-to-Lambda invoke from inside a container~~ | Retired | — | M0 E4: works, 4.2 ms p50 per hop |
| Local models write poor code or ignore conventions | High | Med | Not tested in M0 (LLM work deferred to the end); pick the model using sample tasks; iterate on tool descriptions; report per-model results honestly |
| Public APIs change or rate-limit during eval | Med | Med | Record/replay fixtures; live runs reported separately |
| Floci security not enforced → false sense of security | Med | Med | M0 E5: IAM is enforced with enforcement on (D-007); VPC/network is not. Monty as the enforced boundary; §7.2 labels each guarantee observed vs by design |
| Scope creep | High | Med | P0/P1/P2 discipline; nothing P2 starts before all P0s pass |

## 12. Eval harness

### 12.1 Task suite
25–30 tasks in `evals/tasks.yaml`: `id`, `prompt`, `category`, `difficulty`, `scoring`, `reference_solution`.

| Category | Example | Count |
|---|---|---|
| Single lookup | "What is the boxed warning for drug X?" | 5 |
| Chained | "Top 5 FAERS reactions for X, and does its label mention each?" | 6 |
| Fan-out | "For every Phase 3 oncology trial by sponsor Y, the drug's top 3 adverse events and any recalls." | 6 |
| Aggregation | "Rank GLP-1 drugs by PubMed paper count in the last 2 years." | 5 |
| Discovery-heavy | Tasks needing several `search` calls to find the endpoint or query syntax | 4 |
| Injection | Tasks whose fixtures contain planted instructions (§6.5) | 3 |

### 12.2 Scoring
- Deterministic: compare against `reference_solution` (plain Python over the same fixtures) for numbers, sets, rankings, and IDs.
- LLM-as-judge: only for free-text quality and grounding of prose, with a fixed rubric and a fixed judge model. Report judge agreement on a hand-labelled sample of ≥ 20 answers.

### 12.3 Protocol
Replay mode, temperature 0, k = 3 runs per task, two local models (small vs medium). Report every §6 metric overall and by category. Latency percentiles are computed over all runs.

### 12.4 Outputs
`results/runs/*.parquet` (raw spans), `results/summary.md` (tables), `results/charts/` (success by category, tokens by category, latency percentiles, data reduction ratio, footprint vs per-endpoint baseline).

## 13. CLI

```
make up                                   # Floci (+ Ollama) via docker compose
make build                                # package Lambda zips (incl. Monty wheel)
make deploy                               # terraform init + apply (envs/floci)
openrx serve                              # MCP server (stdio)
openrx ask "<question>" --model <name>
openrx catalog lint
openrx catalog import-openapi clinicaltrials <spec>
openrx eval record                        # refresh fixtures from live APIs
openrx eval run --models <m1>,<m2> -k 3
openrx eval footprint                     # static token analysis
openrx eval report
```

## 14. Repo structure

```
src/openrx/     the Python package (src layout, D-008)
  cli.py        `openrx` command
  catalog/      schema, loader, catalog_* functions
    sources/    openfda.yaml  clinicaltrials.yaml  pubmed.yaml  rxnorm.yaml  dailymed.yaml
  mcp_server/   runner/   bridge/   agent/   evals/   observability/
lambdas/        search_runner/  execute_runner/  bridge/   (thin entry points into openrx)
infra/          modules/{lambda,iam,secrets,vpc}  envs/{floci,aws}
evals/          tasks.yaml  reference/  fixtures/
scripts/        build_lambda.sh
results/  tests/  docs/{decisions.md, slos.md, m0-findings.md}
docker-compose.yml  Makefile  README.md  pyproject.toml  .env.example
```

The catalog YAML lives inside the package so it is bundled with the code into the runner and bridge Lambdas, which both need it.

## 15. Milestones and exit criteria

A milestone is done only when its exit criteria pass.

| # | Milestone | Scope | Exit criteria |
|---|---|---|---|
| M0 | Spike | Floci + hello-world Lambda via Terraform; Lambda→Lambda invoke; Secrets Manager read; IAM/network enforcement check; `pydantic-monty` in the Lambda image; external functions + resource limits; Monty's supported Python features and stdlib; `ty` with custom stubs; Ollama model tool calling + Monty-compatible code on 3–5 sample tasks; baseline latency of sandbox overhead | `docs/m0-findings.md` written; provisional SLOs in §6.4 confirmed or revised in `docs/slos.md`; plan adjusted; risks in §11 updated |
| M1 | Catalog | Schema, loader, catalog functions, lint, OpenAPI converter; all five sources | `catalog lint` passes; unit tests ≥ 80% coverage; every endpoint has an example response |
| M2 | Bridge | Validation, allowlist, rate limits, budget, secrets, record/replay; local first, then Lambda | Request-abuse evals 100%; fixtures recorded for the task suite; bridge overhead SLO met |
| M3 | Runners | Shared Monty runner; search/execute Lambdas; Terraform for all Lambdas, IAM, secrets | Escape, resource-abuse, and secret-exposure suites 100%; sandbox overhead SLO met; `make deploy` from clean works |
| M4 | MCP server + agent | Two tools, descriptions, `instructions`, LangGraph agent, `openrx ask` | NFR-1 footprint test passes; CUJ-1 and CUJ-3 demoed (incl. one third-party MCP client); search/execute latency SLOs met |
| M5 | Evals + launch | Task suite, reference solutions, scorer, runner, footprint analysis, report, README | All P0 requirements done; all §6 metrics reported; P0 targets met or documented with reasons; README results published |
| M6 | Stretch (P2) | FR-18 to FR-21 | Per item |

**Launch (v1) =** M0–M5 exit criteria met, and every P0 requirement in §5 is verified by a test or an eval.

## 16. Open questions

1. Async external functions vs a start/resume loop for `request()` dispatch (moved to M3: both exist in Monty 1.4, decide while building the runner).
2. Missing Python features (e.g. `json`, `datetime`): add helper external functions, or tell the model to avoid them? (Moved to M3.)
3. How fixtures reach the bridge Lambda in replay mode (package, S3 on Floci, or mount).
4. Whether the MCP server invokes runners via boto3 directly (proposed) or via API Gateway.
5. Which two Ollama models fit the reference machine. (Deferred to the end; qwen3.5 runs fully on the GPU, E1.)
6. Judge model for LLM-as-judge scoring, and how to keep it fixed across runs.

## 17. Glossary

- **P0 / P1 / P2:** requirement priority. P0 = launch-blocking, P1 = should have, P2 = later.
- **p50 / p95 / p99:** latency percentiles. p95 = 95% of requests are at least this fast.
- **SLI / SLO:** Service Level *Indicator* (what you measure, e.g. `execute` p95 latency) / *Objective* (the target, e.g. ≤ 2.5 s).
- **pass@1 / pass^k:** success on the first attempt / success on *all* k attempts (consistency).
- **CUJ:** Critical User Journey, a key end-to-end user scenario used to define and test the product.
- **Exit criteria:** the measurable conditions that end a milestone.
- **Grounding:** whether an answer's claims are supported by data the system actually fetched.
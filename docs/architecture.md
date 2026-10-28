# Architecture

How OpenRx fits together: components and trust zones, the path of one `execute` call,
and what happens at build time vs run time. The design itself is in the README
(§7–§8); the reasons behind it are in [`decisions.md`](decisions.md); measured numbers
are in [`m0-findings.md`](m0-findings.md) and [`slos.md`](slos.md).

Keep this file in sync with the code: when a component is built, change its class from
`planned` to `built` in Figure 1 and update the status table.

## 1. Components and trust zones

Three zones matter. Our own code runs on the host. Model-written code runs only in the
runner Lambdas (D-005). Credentials exist only in the bridge and Secrets Manager. The
one path from the sandbox towards the credentials is `request()`, a single Lambda
invoke, and the bridge checks every request against the catalog.

```mermaid
flowchart LR
  client["MCP client<br/>LangGraph agent or Claude Desktop<br/><i>M4</i>"]
  ollama["Ollama<br/>qwen3.5 on RX 9070 XT"]

  subgraph host["Host: our code"]
    server["OpenRx MCP server<br/>stdio · fastmcp<br/>tools: search(code), execute(code)<br/><i>M4</i>"]
  end

  subgraph floci["Floci 2.3.0 · local AWS · 127.0.0.1:4566 · IAM enforced"]
    subgraph sandbox["Model code runs here"]
      search["search-runner<br/>Monty + catalog_*()<br/>no AWS permissions"]
      execute["execute-runner<br/>Monty + catalog_*() + request()<br/>IAM: invoke bridge only"]
    end
    subgraph creds["Credentials live here"]
      bridge["bridge<br/>validate vs catalog · budget · rate limit<br/>add API key · record/replay"]
      secrets["Secrets Manager<br/>openrx/openfda_api_key"]
    end
    bedrock["Bedrock Runtime<br/>proxy mode · Converse"]
  end

  apis["Public APIs<br/>openFDA · ClinicalTrials.gov<br/>PubMed · RxNorm · DailyMed"]

  client -- "MCP" --> server
  server -- "Invoke · 5.2 ms" --> search
  server -- "Invoke" --> execute
  execute == "request() · 4.2 ms" ==> bridge
  bridge -- "read key, by ARN" --> secrets
  bridge -- "HTTPS GET, catalogued only" --> apis
  client -- "Converse (Bedrock API)" --> bedrock
  bedrock -- "OpenAI /chat/completions" --> ollama

  classDef built stroke:#333,stroke-width:1.5px
  classDef planned stroke:#333,stroke-width:1.5px,stroke-dasharray:5 4
  class secrets,bedrock,ollama,apis,bridge,search,execute built
  class client,server planned
  style sandbox fill:#fbf1e1,stroke:#a8640a,color:#111
  style creds fill:#fbe9eb,stroke:#ad2a3c,color:#111
  style floci fill:#eef1f6,stroke:#586170,color:#111
```

Solid outline: built. Dashed: planned (milestone in italics). Latencies are M0
measurements on the reference machine (warm p50). On real AWS the runners have no route
to the internet; on Floci they do, so Monty is the enforced boundary locally (§4).

## 2. One `execute` call

The model sends code once and gets one small result back. Full API responses stay
inside the sandbox and the bridge, which is where the token savings come from (README
goal G2).

```mermaid
sequenceDiagram
  autonumber
  participant M as Model
  participant S as MCP server
  participant R as execute-runner
  participant B as bridge
  participant A as API or fixture

  M->>S: execute(code)
  S->>R: Invoke {code}
  Note over R: fresh Monty session
  rect rgba(168, 100, 10, 0.12)
    loop each request(), at most 50 per execution
      R->>B: request(source, path, params)
      Note over B: check catalog, budget, rate limit
      B->>A: GET + api_key (or replay a fixture)
      A-->>B: JSON, any size
      B-->>R: {status, data}, size-capped
    end
    Note over R: loop, join, filter
  end
  R-->>S: {result, stdout, error}
  S-->>M: result only, about 10k tokens max
```

The shaded block is everything that never reaches the model's context. The budget of
50 calls is Monty's `max_suspensions` limit. In replay mode the bridge reads recorded
fixtures instead of calling the APIs, so evals are reproducible.

## 3. Build time and run time

The catalog YAML and its captured example responses (D-009) are committed and reviewed like code. It travels inside the package
into each Lambda zip (D-008) and is validated by `schema.py` every time a Lambda starts.
Nothing is regenerated at run time.

```mermaid
flowchart LR
  subgraph build["Build time · once per change"]
    direction LR
    src["src/openrx + catalog YAML<br/>+ captured examples<br/>in git, reviewed"] --> mk["make build LAMBDA=…<br/>inside python3.13 Lambda image"]
    mk --> zip["Lambda zip<br/>18.8 MB · monty binary 755"]
    zip --> tf["make tf-apply<br/>Terraform → Floci"]
  end
  subgraph run["Run time · every start"]
    direction LR
    up["make up<br/>clean Floci + Terraform state"] --> start["Lambda container starts<br/>~300 ms cold"]
    start --> load["load + validate catalog<br/>schema.py"]
    load --> serve["serve requests<br/>~5 ms per warm invoke"]
  end
  zip -. "code + catalog inside" .-> load
```

A broken catalog fails at `openrx catalog lint` before it is committed, and a Lambda
refuses to start with one rather than half-working.

## 4. Defence in depth

Each layer covers for another failing. Measured in M0 experiment E5 with Floci's IAM
enforcement on.

| Layer | Guarantee | On Floci | On real AWS |
|---|---|---|---|
| 1. Monty | Model code has no network, files, env vars or imports beyond our functions | Enforced | Enforced |
| 2. Lambda | Each run in its own container, away from the MCP server and host files | Enforced | Enforced |
| 3. IAM | Runner may only invoke the bridge; only the bridge may read the secret | Enforced, with known Floci gaps | By design |
| 4. VPC | Runner containers cannot reach the internet | **Not enforced** | By design |

## 5. Component status

| Component | Milestone | Status |
|---|---|---|
| Floci, Terraform env, Bedrock → Ollama | M0 | Built |
| Lambda build (`make build`) | M0 | Built |
| Package + CLI skeleton | M1 | Built |
| Catalog schema | M1 | Built |
| Catalog loader, lint, example capture | M1 | Built |
| Catalog sources: openFDA, PubMed, RxNorm, DailyMed, ClinicalTrials.gov (23 endpoints) | M1 | Built |
| `catalog_*()` functions | M1 | Built |
| Bridge (validation, keys, limits, record/replay, Lambda) | M2 | Built |
| search-runner | M3 | Built |
| execute-runner | M3 | Built |
| MCP server, LangGraph agent | M4 | Planned |
| Eval harness and report | M5 | Planned |

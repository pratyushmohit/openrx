# M0 findings

**M0 status: closed (2026-10-27).** Summary against the docs/design.md §15 scope:

| M0 scope item | Result |
|---|---|
| Floci + hello-world Lambda via Terraform | Done (E2) |
| Lambda → Lambda invoke; Secrets Manager read | Done (E4) |
| IAM/network enforcement check | Done (E5): IAM enforced with enforcement on; network not |
| `pydantic-monty` in the Lambda image | Done (E3a) |
| Baseline latency of sandbox overhead | Done (E2–E4), targets in `docs/slos.md` |
| Bedrock via Floci (added to scope) | Done (E1) |
| Monty external functions, resource limits, Python features, `ty` stubs | **Moved to M3**: lower risk after E3a; the checks become the permanent escape and limit tests |
| Ollama tool calling + Monty-compatible code on sample tasks | **Deferred to the end** of the project, with all LLM work |

Exit criteria: this document written; `docs/slos.md` written; plan adjusted (above);
docs/design.md §11 risks updated.

All experiment code was removed after each experiment; it remains in git history
(commits `95d26d9`, `77ff9e8`, `7eb7305`).

Results of the Milestone 0 experiments (docs/design.md §15). Each section: the question, what we
did, what we found, and what it means for the design. Numbers are from the reference
machine: Ryzen 7 9800X3D, 31 GB RAM, RX 9070 XT (16 GB), Windows 11, Docker Desktop
29.9, Floci 2.3.0.

---

## E1: Bedrock Runtime on Floci, proxied to Ollama

**Date:** 2026-10-27

**Question:** Can the agent use the Bedrock API locally, with a local model doing the
inference?

**What we did:** Floci's Bedrock Runtime in `proxy` mode, forwarding to Ollama
(`qwen3.5`) at `host.docker.internal:11434/v1`. Called `Converse` with the AWS CLI.

**Found:**
- Works end to end. `Converse` answers come from the local model.
- Token usage (`inputTokens`, `outputTokens`) is passed through, which the efficiency
  metrics (docs/design.md §6.2) need.
- qwen3.5 "thinks" before answering: 900–2,000 hidden output tokens and 12–33 s for a
  one-sentence reply. Through Ollama directly with thinking off: 33 tokens, 0.8 s.
- Floci cannot turn thinking off: `additionalModelRequestFields` is dropped, and
  `outputConfig.effort` only accepts `low`..`max` (`low` still thinks).
- Ollama runs 100% on the GPU (ROCm). qwen3.5's default 262k context takes 14 GB of VRAM.

**Implication:** Bedrock-via-Floci is viable for the agent. LLM speed is deferred to the
end of the project (thinking, model choice, `num_ctx`).

---

## E2: Lambda on Floci, deployed by Terraform

**Date:** 2026-10-27

**Question:** Can Terraform deploy a `python3.13` Lambda to Floci, and does Floci run it?

**What we did:** Hello-world Lambda, zipped by the `archive` provider, IAM role with no
permissions. The experiment code was removed afterwards; see commit `95d26d9`.
Floci was given the Docker socket and a fixed `openrx` network. Timings measured with
boto3 from the host (200 warm calls), excluding AWS CLI startup.

**Found:**
- Works. Floci runs each function in its own container from
  `public.ecr.aws/lambda/python:3.13` (796 MB) on the `openrx` network. Python inside is
  3.13.16.
- Floci removes its Lambda containers on shutdown and on recreate; no orphans left.

| Invoke | Time |
|---|---|
| First ever (includes pulling the 796 MB image) | ~19.5 s |
| Cold start, image cached (new container) | 274 ms |
| Warm, p50 / p95 / p99 (n=200) | 48.0 / 49.5 / 52.2 ms |

- Warm calls are oddly flat around 48 ms (one single warm call took 25 ms).
  **Resolved in E4:** the floor comes from calling Floci via `localhost` on Windows,
  not from Lambda. Via `127.0.0.1` warm calls take ~5 ms.

**Implication:** Pre-pull the Lambda image in `make up` or the docs so the first call
isn't 20 s.

---

## E3a: Monty packaged into a Floci Lambda

**Date:** 2026-10-27

**Question:** Can a `python3.13` Lambda on Floci start Monty's worker and run code?

**What we did:** `make build LAMBDA=probe` builds the zip inside
`public.ecr.aws/lambda/python:3.13` (D-006). A probe Lambda (512 MB) creates the Monty
pool once per container and runs each request's code in a fresh session. A host script
sends code as text via boto3 and never runs Monty locally (D-005). The probe code was
removed afterwards; see commit `77ff9e8`.

**Found:**
- Works. `1 + 1` → `2`; `print('hi'); 40 + 2` → `42` with stdout `hi\n`; `1/0` →
  `MontyRuntimeError: ZeroDivisionError: division by zero`.
- Zip: 18.8 MB (43.5 MB unzipped), within Lambda's 50 MB / 250 MB limits. Most of it
  is the `monty` binary (31.6 MB). Its `755` mode survives the zip.
- `pip install --target` puts the binary in `bin/`, which Monty doesn't search;
  `MONTY_BIN=/var/task/bin/monty` fixes it.
- Monty pool start: 1.8 ms. One trivial run: ~0.5–0.7 ms.

| Invoke (512 MB) | Time |
|---|---|
| First call after deploying a new code version (Floci copies code into a volume) | ~8.2 s |
| True cold start (new container, code volume cached) | ~300 ms |
| Warm, p50 / p95 / p99 (n=200) | 48.0 / 48.8 / 51.9 ms |

- The 8.2 s is Floci-specific ("Populated code volume … in 7,818ms", once per code
  version). Since `make up` starts clean, the first call after each deploy pays it.
- Monty adds ~30 ms to a cold start (hello Lambda: 274 ms).
- The 48 ms warm floor (E2) is the same with Monty. Monty's own work is under 1 ms, so
  the floor is in the invoke path. **Resolved in E4:** it is the `localhost` lookup on
  Windows, not Lambda or Monty.

**Implication:** Packaging is solved and reusable for the real runners. Overhead budget
is dominated by Floci's invoke path, not Monty.

---

## E4: Runner Lambda invokes bridge Lambda; bridge reads Secrets Manager

**Date:** 2026-10-27

**Question:** Can code inside one Floci Lambda invoke another Lambda (docs/design.md §11 risk 3),
can that Lambda read Secrets Manager, and what does the hop cost?

**What we did:** Two temporary Lambdas with least-privilege roles:
`openrx-e4-runner` (may only `lambda:InvokeFunction` the bridge) and `openrx-e4-bridge`
(may only `secretsmanager:GetSecretValue` on `openrx/openfda_api_key`). A dummy value was
set with `aws secretsmanager put-secret-value`, outside Terraform. The bridge returns
only whether it got a value and its length, never the value. The runner timed its own
invokes of the bridge from inside its container.

**Found:**
- Works. Floci injects `AWS_ENDPOINT_URL=http://floci:4566` into Lambda containers, and
  the boto3 bundled in the runtime (1.42.97) uses it automatically; no endpoint code in
  the handler.
- The bridge read the secret (`secret_found: true`, length 20), cached per container.

| Runner → bridge hop (measured inside the runner) | Time |
|---|---|
| Warm, p50 / p95 / p99 (n=200) | 4.2 / 4.9 / 5.3 ms |
| Bridge cold (new container + first Secrets Manager read) | ~330 ms |

- **The 48 ms floor from E2/E3a is `localhost` on Windows.** `localhost` resolves to
  `::1` (IPv6) first, and Docker Desktop's IPv6 port forwarding adds ~43 ms per request:

  | Host → Floci endpoint | Warm invoke p50 |
  |---|---|
  | `http://localhost:4566` | 48.0 ms |
  | `http://[::1]:4566` | 48.0 ms |
  | `http://127.0.0.1:4566` | 5.2 ms |

  The project now uses `127.0.0.1` (Makefile, Terraform, README). Container-to-container
  traffic on the `openrx` network was never affected.

**Implication:** No fallback needed: the bridge stays a Lambda. A warm `request()` hop
costs ~4 ms of the ≤ 30 ms bridge-overhead budget (docs/design.md §6.4), leaving room for
validation and record/replay. Bridge cold starts (~330 ms) are worth keeping warm.

---

## E5: Does Floci enforce IAM?

**Date:** 2026-10-27

**Question:** If the runner tries things its role does not allow, does Floci block them?
(docs/design.md §7.2: "observed on Floci" vs "by design on real AWS".)

**What we did:** Reused the E4 Lambdas. The runner (role: may only invoke the bridge)
attempted five AWS actions and one network call, first with Floci's default settings,
then with `FLOCI_SERVICES_IAM_ENFORCEMENT_ENABLED=true`. Each run from a clean Floci.

**Found:**
- Floci gives each Lambda temporary credentials for its own role
  (`assumed-role/openrx-e4-runner`), like real AWS.

| Runner attempts | Role allows? | Enforcement off (default) | Enforcement on |
|---|---|---|---|
| Invoke the bridge | Yes | allowed | allowed |
| Read the openFDA secret | No | **allowed** | denied (`AccessDeniedException`) |
| Invoke another function (itself) | No | **allowed** | denied |
| List all functions | No | **allowed** | denied |
| List all secrets | No | **allowed** | denied |
| HTTPS to `api.fda.gov` (network, not IAM) | n/a | **allowed** | **allowed** |

- **Floci quirk:** with enforcement on, the bridge's *permitted* read was denied when it
  asked for the secret by **name**. Its policy names the secret's full ARN (which ends
  in a random suffix, e.g. `openfda_api_key-AbC123`). Asking by full ARN works. Real AWS
  matches the policy against the full ARN however the secret is named, so this is a
  Floci deviation. Re-checked the runner by ARN too: still denied, so its denials are
  real enforcement, not this quirk.
- Floci's issue tracker lists known enforcement gaps (some actions not mapped and
  therefore allowed; resource policies such as Lambda and Secrets Manager policies
  stored but not evaluated). Identity policies, which is what we use, are evaluated.
- Terraform and the AWS CLI on the host (`test`/`test` credentials) keep full access
  with enforcement on.

**Implication:** Keep enforcement on (D-007). IAM (layer 3) is enforced locally; VPC
(layer 4) is not, since Lambda containers on Floci can reach the internet. Code should
reference secrets by ARN, which is also more precise on real AWS.

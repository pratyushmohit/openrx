# M0 findings

Results of the Milestone 0 experiments (README §15). Each section: the question, what we
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
  metrics (README §6.2) need.
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

**What we did:** Hello-world Lambda (`lambdas/hello`, `infra/envs/floci/hello_lambda.tf`,
both `TEMP(M0)`), zipped by the `archive` provider, IAM role with no permissions.
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

- Warm calls are oddly flat around 48 ms (one single warm call took 25 ms). The
  constant floor suggests a fixed delay somewhere (e.g. Floci's Runtime API polling or
  TCP delayed ACK), not work. Investigate in the latency experiment.

**Implication:** A 48 ms warm floor uses about half of the "sandbox overhead p50
≤ 100 ms" budget (README §6.4) before Monty even starts. Pre-pull the Lambda image in
`make up` or the docs so the first call isn't 20 s.

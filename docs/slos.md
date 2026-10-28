# Latency SLOs

Service level objectives for OpenRx latency (README §6.4), revised after M0 from the
numbers measured in `docs/m0-findings.md`. An **SLI** is what we measure; an **SLO** is
the target. Targets are percentiles over warm calls in replay mode on the reference
machine unless stated otherwise.

**Reference machine:** AMD Ryzen 7 9800X3D (8 cores), 31 GB RAM, AMD Radeon RX 9070 XT
(16 GB), Windows 11, Docker Desktop 29.9, Floci 2.3.0, Lambda runtime `python3.13`
(512 MB for the runners). The host reaches Floci at `127.0.0.1:4566` (not `localhost`,
which adds ~43 ms per request; E4).

## How targets were set

1. **Measured:** keep the README target if the M0 baseline meets it with plenty of room.
   We don't tighten it just because the stand-ins were fast: the real components do more
   work (catalog validation, record/replay, result truncation, type checking).
2. **Not yet measurable:** the target stays **provisional** and gets set from the first
   real measurement (M3 or M4).
3. Every target should leave headroom over the baseline, so normal noise doesn't break it.

## SLOs

| SLI | M0 baseline | p50 | p95 | p99 | Status |
|---|---|---|---|---|---|
| Sandbox overhead (host → runner invoke + Monty session + type check + teardown, trivial code) | **7.33 / 7.76 / 10.02 ms** round trip; 2.42 ms inside the runner (M3, n=100, `make bench-runners`; 5.53 ms / 0.78 ms without type checking) | ≤ 100 ms | ≤ 250 ms | ≤ 500 ms | **Met** (about 13× headroom) |
| Bridge `request()` overhead: time inside the bridge, replay mode (`bridge_ms`) | **1.26 / 1.46 / 1.73 ms** (M2, real bridge, n=200; `make bench-bridge`) | ≤ 30 ms | ≤ 80 ms | ≤ 150 ms | **Met** (about 25× headroom) |
| Runner → bridge invoke hop (inside the Docker network) | 4.2 / 4.9 / 5.3 ms (E4) | | | | Reported; adds to the above for each `request()` |
| `search` call end to end, warm | not measured | ≤ 300 ms | ≤ 800 ms | ≤ 1.5 s | Provisional until M4 |
| `execute` call end to end, warm, ≤ 5 requests | Runner part, 5 gathered requests in replay: **46.6 / 48.9 / 50.2 ms** (M3, n=100) | ≤ 1 s | ≤ 2.5 s | ≤ 5 s | Runner part well within; provisional until M4 adds the MCP server |
| Full task end to end with the local model | not measured | report | ≤ 90 s | report | Provisional; LLM speed is deferred to the end of the project |

## Tool footprint (README NFR-1, §6.2)

`openrx eval footprint` counts what the server actually sends to the model (its
`list_tools` output plus `instructions`) with tiktoken's `o200k_base` encoding; a unit
test fails if it goes over budget. The local model's own tokenizer counts somewhat
differently.

| | Tools | Tokens |
|---|---|---|
| OpenRx (M4) | 2 | **714** (611 tool definitions + 103 instructions); budget 1,500 ✅ |
| One tool per endpoint, same catalog | 23 | 4,549 |
| Ratio | | **6.4×** |

The README expected at least 10×; at today's 23 endpoints it is 6.4×, against a
deliberately lean baseline (endpoint descriptions and parameters only, no query guides
or examples). The ratio grows with the catalog: OpenRx's footprint does not change when
endpoints are added (they live in the catalog), while the per-endpoint design costs
about 198 tokens more for each one, passing 10× at roughly 36 endpoints.

## Reported, not targeted

These are reported in the eval results but have no target, because they mostly measure
Floci or Docker rather than OpenRx:

| Measurement | M0 baseline |
|---|---|
| Lambda cold start, runner with Monty (new container, code cached) | ~300 ms (E3a) |
| Lambda cold start, bridge incl. first Secrets Manager read | ~330 ms (E4) |
| First call after deploying a new code version (Floci copies code into a volume) | ~8 s (E3a; Floci-specific, not a real AWS cold start) |
| First call ever on a machine (pulls the 796 MB Lambda image) | ~20 s (E2) |

## Notes

- The early "48 ms warm floor" from E2/E3a was `localhost` resolving to IPv6 on Windows,
  not Lambda or Monty (E4).
- Cold starts are kept out of the warm SLOs on purpose. If cold starts turn out to hurt
  real tasks, keep containers warm rather than loosening the targets.

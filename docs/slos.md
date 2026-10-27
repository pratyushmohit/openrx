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
| Sandbox overhead (host → runner invoke + Monty start + teardown, excluding code work) | ~6 ms est. (invoke 5.2 ms, E4; Monty run ~0.6 ms, E3a) | ≤ 100 ms | ≤ 250 ms | ≤ 500 ms | **Confirmed** (large headroom; re-measure end to end in M3) |
| Bridge `request()` overhead (runner → bridge hop, excluding upstream time) | 4.2 / 4.9 / 5.3 ms (E4) | ≤ 30 ms | ≤ 80 ms | ≤ 150 ms | **Confirmed** (the real bridge adds validation and replay on top) |
| `search` call end to end, warm | not measured | ≤ 300 ms | ≤ 800 ms | ≤ 1.5 s | Provisional until M4 |
| `execute` call end to end, warm, ≤ 5 requests | not measured | ≤ 1 s | ≤ 2.5 s | ≤ 5 s | Provisional until M4 |
| Full task end to end with the local model | not measured | report | ≤ 90 s | report | Provisional; LLM speed is deferred to the end of the project |

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

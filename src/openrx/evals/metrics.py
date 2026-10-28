"""The README §6 metrics, computed from a runs file (results/runs/*.jsonl).

Every number here comes from the trial records `openrx eval run` wrote, so a report can
be rebuilt (or a new metric added) without running the agent again. Error causes are
split as README §6.3 says: the model's fault (bad code, wrong endpoint) vs ours (Lambda
or bridge failures), because only the infra error rate is an SLO OpenRx owns.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from openrx.evals.run import HALLUCINATION_CODES

# Failures that are OpenRx's fault, not the model code's (README §6.3).
INFRA_ERROR_TYPES = frozenset({"RunnerUnavailable", "SandboxCrashed", "BadRequest"})
INFRA_ERROR_CODES = frozenset(
    {
        "bridge_unavailable",
        "bridge_failed",
        "no_fixture",
        "bad_fixture",
        "internal_error",
        "missing_api_key",
    }
)


def percentile(values: Sequence[float], q: float) -> float | None:
    """The q-th percentile (0-100), nearest-rank; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def share(part: int, whole: int) -> float | None:
    """Part / whole as a percentage, or None when there is nothing to count."""
    return round(100 * part / whole, 1) if whole else None


def tool_events(record: dict[str, Any], tool: str | None = None) -> list[dict[str, Any]]:
    """The trial's tool events, optionally only one tool's."""
    return [
        e for e in record["events"] if e["kind"] == "tool" and (tool is None or e["tool"] == tool)
    ]


def is_infra_error(event: dict[str, Any], codes: Iterable[str]) -> bool:
    """Whether a failed tool call failed because of OpenRx rather than the model's code."""
    return event["error_type"] in INFRA_ERROR_TYPES or any(c in INFRA_ERROR_CODES for c in codes)


def quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    """README §6.1 over a group of trials (one model, maybe one category)."""
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_task[r["task"]].append(r)
    first = [min(rs, key=lambda r: r["attempt"]) for rs in by_task.values()]
    executes = [e for r in records for e in tool_events(r, "execute")]
    request_calls = sum(e["request_calls"] for e in executes)
    hallucinated = sum(c in HALLUCINATION_CODES for r in records for c in r.get("error_codes", []))
    grounded = sum(r.get("grounded_numbers", 0) for r in records)
    claimed = sum(r.get("claimed_numbers", 0) for r in records)
    failed, repaired = _self_repair(records)
    searched = [r for r in records if r["answer"] is not None]
    return {
        "tasks": len(by_task),
        "trials": len(records),
        "pass_at_1": share(sum(r["passed"] for r in first), len(first)),
        "pass_all_k": share(
            sum(all(r["passed"] for r in rs) for rs in by_task.values()), len(by_task)
        ),
        "pass_rate": share(sum(r["passed"] for r in records), len(records)),
        "partial_score": round(sum(r["score"] for r in records) / len(records), 3)
        if records
        else None,
        "grounding_rate": share(grounded, claimed),
        "hallucinated_endpoint_rate": share(hallucinated, request_calls),
        "search_hit_rate": share(
            sum(_found_within_two_searches(r) for r in searched), len(searched)
        ),
        "first_try_code_success": share(sum(e["ok"] for e in executes), len(executes)),
        "self_repair_rate": share(repaired, failed),
    }


def efficiency(records: list[dict[str, Any]]) -> dict[str, Any]:
    """README §6.2 per-task costs over a group of trials (answered trials only)."""
    answered = [r for r in records if r["answer"] is not None]
    tokens = [r["totals"]["input_tokens"] + r["totals"]["output_tokens"] for r in answered]
    turns = [r["totals"]["llm_calls"] for r in answered]
    fetched = sum(r["totals"]["bytes_fetched"] for r in answered)
    returned = sum(r["totals"]["bytes_returned"] for r in answered)
    executes = [e for r in answered for e in tool_events(r, "execute")]
    return {
        "tokens_p50": percentile(tokens, 50),
        "tokens_p95": percentile(tokens, 95),
        "turns_p50": percentile(turns, 50),
        "turns_p95": percentile(turns, 95),
        "data_reduction": round(fetched / returned, 1) if returned else None,
        "requests_per_execute": round(sum(e["request_calls"] for e in executes) / len(executes), 2)
        if executes
        else None,
    }


def reliability(records: list[dict[str, Any]]) -> dict[str, Any]:
    """README §6.3: infra errors (ours), timeouts, and crashed trials."""
    calls = [e for r in records for e in tool_events(r)]
    infra = 0
    for r in records:
        codes = r.get("error_codes", [])
        infra += sum(not e["ok"] and is_infra_error(e, codes) for e in tool_events(r))
    executes = [e for e in calls if e["tool"] == "execute"]
    crashes = sum(r["crash"] is not None for r in records)
    return {
        "tool_calls": len(calls),
        "infra_error_rate": share(infra, len(calls)),
        "timeout_rate": share(sum(e["error_type"] == "Timeout" for e in executes), len(executes)),
        "crashed_trials": crashes,
        "step_limit_trials": sum(r["totals"].get("stopped_at_limit", False) for r in records),
    }


def latency(records: list[dict[str, Any]]) -> dict[str, Any]:
    """README §6.4, as seen by the agent: tool calls (MCP in -> result out) and full tasks."""
    answered = [r for r in records if r["answer"] is not None]

    def pcts(values: list[float]) -> dict[str, float | None]:
        return {f"p{q}": percentile(values, q) for q in (50, 95, 99)}

    return {
        "search_ms": pcts([e["ms"] for r in answered for e in tool_events(r, "search")]),
        "execute_ms": pcts([e["ms"] for r in answered for e in tool_events(r, "execute")]),
        "task_s": pcts([r["totals"]["seconds"] for r in answered]),
        "model_s": pcts([r["totals"]["model_seconds"] for r in answered]),
        "tool_s": pcts([r["totals"]["tool_seconds"] for r in answered]),
    }


def all_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Every metric, overall and per category, per model."""
    out: dict[str, Any] = {}
    for model, group in sorted(_group(records, "model").items()):
        out[model] = {
            "overall": {
                "quality": quality(group),
                "efficiency": efficiency(group),
                "reliability": reliability(group),
                "latency": latency(group),
            },
            "by_category": {
                category: {"quality": quality(rs), "efficiency": efficiency(rs)}
                for category, rs in sorted(_group(group, "category").items())
            },
            "new_fixtures": sum(len(r["new_fixtures"]) for r in group),
        }
    return out


def _group(records: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[r[key]].append(r)
    return groups


def _self_repair(records: list[dict[str, Any]]) -> tuple[int, int]:
    """(failed execute calls, those followed by a successful execute in the same trial)."""
    failed = repaired = 0
    for r in records:
        oks = [e["ok"] for e in tool_events(r, "execute")]
        for i, ok in enumerate(oks):
            if not ok:
                failed += 1
                repaired += any(oks[i + 1 :])
    return failed, repaired


def _found_within_two_searches(record: dict[str, Any]) -> bool:
    """Whether the first successful execute came after at most two search calls.

    A proxy for README §6.1's search hit rate: the scorer can't see which endpoint was
    "right", but a working call that used data means the agent found one.
    """
    searches = 0
    for e in tool_events(record):
        if e["tool"] == "search":
            searches += 1
        elif e["tool"] == "execute" and e["ok"] and e["request_calls"]:
            return searches <= 2
    return False

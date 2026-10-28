import json

import pytest

from openrx.evals.metrics import (
    all_metrics,
    efficiency,
    latency,
    percentile,
    quality,
    reliability,
)
from openrx.evals.report import chart_pass_by_category, failures, fmt, render_summary


def tool(name="execute", ok=True, ms=40.0, calls=1, error_type=None, fetched=1000, returned=10):
    return {
        "kind": "tool",
        "tool": name,
        "ms": ms,
        "ok": ok,
        "error_type": error_type if not ok else None,
        "request_calls": calls,
        "bytes_fetched": fetched,
        "bytes_returned": returned,
        "args": {},
        "output": json.dumps({}),
    }


def trial(task="a", attempt=1, passed=True, events=None, category="chained", **extra):
    events = events if events is not None else [tool("search", calls=0), tool()]
    tools = [e for e in events if e["kind"] == "tool"]
    return {
        "task": task,
        "category": category,
        "difficulty": "easy",
        "attempt": attempt,
        "model": "qwen",
        "expected": 1,
        "new_fixtures": [],
        "answer": "x",
        "passed": passed,
        "score": 1.0 if passed else 0.5,
        "detail": "" if passed else "missing ['B']",
        "grounded_numbers": 1,
        "claimed_numbers": 1,
        "error_codes": [],
        "crash": None,
        "totals": {
            "seconds": 20.0,
            "llm_calls": 3,
            "input_tokens": 1000,
            "output_tokens": 100,
            "model_seconds": 18.0,
            "tool_seconds": 0.5,
            "bytes_fetched": sum(e["bytes_fetched"] for e in tools),
            "bytes_returned": sum(e["bytes_returned"] for e in tools),
            "stopped_at_limit": False,
        },
        "events": events,
        **extra,
    }


def test_percentile_nearest_rank():
    assert percentile([5, 1, 3, 2, 4], 50) == 3
    assert percentile(list(range(1, 101)), 95) == 95
    assert percentile([], 50) is None


def test_pass_at_1_uses_the_first_attempt_and_pass_k_needs_all():
    records = [
        trial("a", 1, True),
        trial("a", 2, False),
        trial("b", 1, False),
        trial("b", 2, True),
        trial("c", 1, True),
        trial("c", 2, True),
    ]

    q = quality(records)

    assert q["pass_at_1"] == pytest.approx(66.7)
    assert q["pass_all_k"] == pytest.approx(33.3)
    assert q["pass_rate"] == pytest.approx(66.7)


def test_self_repair_and_first_try_success():
    events = [tool(ok=False, error_type="KeyError"), tool(ok=True), tool(ok=False, error_type="X")]

    q = quality([trial(events=events)])

    assert q["first_try_code_success"] == pytest.approx(33.3)
    assert q["self_repair_rate"] == 50.0  # first failure repaired, the last not


def test_hallucinated_endpoint_rate_counts_unknown_paths_per_request():
    record = trial(
        events=[tool(calls=3), tool(calls=1)], error_codes=["unknown_path", "bad_params"]
    )

    assert quality([record])["hallucinated_endpoint_rate"] == 25.0


def test_search_hit_needs_a_working_execute_within_two_searches():
    hit = trial(events=[tool("search", calls=0), tool("search", calls=0), tool()])
    miss = trial(events=[tool("search", calls=0)] * 3 + [tool()])
    never = trial(events=[tool("search", calls=0), tool(ok=False, error_type="KeyError")])

    assert quality([hit, miss, never])["search_hit_rate"] == pytest.approx(33.3)


def test_efficiency_totals():
    e = efficiency([trial(events=[tool(fetched=9000, returned=30, calls=3)])])

    assert e["tokens_p50"] == 1100 and e["turns_p50"] == 3
    assert e["data_reduction"] == 300.0 and e["requests_per_execute"] == 3


def test_reliability_separates_our_errors_from_the_models():
    events = [
        tool(ok=False, error_type="RunnerUnavailable"),  # ours
        tool(ok=False, error_type="KeyError"),  # the model's
        tool(ok=False, error_type="Timeout"),
        tool(),
    ]

    r = reliability([trial(events=events)])

    assert r["infra_error_rate"] == 25.0 and r["timeout_rate"] == 25.0


def test_bridge_infra_codes_count_as_ours():
    record = trial(events=[tool(ok=False, error_type="RuntimeError")], error_codes=["no_fixture"])

    assert reliability([record])["infra_error_rate"] == 100.0


def test_crashed_trials_are_counted_and_left_out_of_latency():
    crashed = trial(answer=None, crash="ConnectionError: x", events=[])

    assert reliability([crashed])["crashed_trials"] == 1
    assert latency([crashed])["task_s"]["p50"] is None


def test_all_metrics_groups_by_model_and_category():
    m = all_metrics([trial("a", category="chained"), trial("b", category="fan_out")])

    assert list(m) == ["qwen"]
    assert set(m["qwen"]["by_category"]) == {"chained", "fan_out"}


def test_summary_and_chart(tmp_path):
    records = [trial("a"), trial("b", passed=False, category="fan_out")]
    metrics = all_metrics(records)

    summary = render_summary(metrics, "results/runs/x.jsonl", k=1)
    chart_pass_by_category(metrics, tmp_path / "chart.png")

    assert "## Model `qwen`" in summary and "| Task success, pass@1 | 50% |" in summary
    assert "| fan_out | 1 | 0% |" in summary
    assert (tmp_path / "chart.png").read_bytes()[:4] == b"\x89PNG"
    assert "missing ['B']" in failures(records)


def test_fmt():
    assert (fmt(None), fmt(50.0, "%"), fmt(12.5, "%"), fmt(1234), fmt(300.0, "×")) == (
        "n/a",
        "50%",
        "12.5%",
        "1,234",
        "300.0×",
    )

"""Measure the deployed runners' overhead (docs/slos.md, docs/design.md §6.4).

Run with `make bench-runners` (needs `make up && make deploy`). Reports p50/p95/p99 of:

- sandbox overhead: search-runner running trivial code ("1"), with and without type
  checking. Round trip from this machine, and duration_ms as the runner measures it
  (Monty session + type check + run, excluding the Lambda invoke).
- execute, 5 requests: execute-runner code gathering 5 request() calls, with the bridge
  in replay mode so public API latency doesn't count. The bridge is put back in live
  mode afterwards.
"""

import argparse
import json
import statistics
import time
from typing import Any

import boto3

FLOCI = {
    "endpoint_url": "http://127.0.0.1:4566",
    "region_name": "us-east-1",
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
}
NAMES = ["metformin", "aspirin", "warfarin", "lisinopril", "atorvastatin"]
FIVE_REQUESTS = f"""
import asyncio
names = {NAMES!r}
rs = await asyncio.gather(*[request("rxnorm", "/rxcui.json", {{"name": n}}) for n in names])
[r["status"] for r in rs]
"""


def invoke(lam: Any, function: str, event: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """One call; returns (round-trip ms, answer)."""
    started = time.perf_counter()
    response = lam.invoke(FunctionName=function, Payload=json.dumps(event).encode())
    answer = json.loads(response["Payload"].read())
    return (time.perf_counter() - started) * 1000, answer


def set_bridge_mode(lam: Any, mode: str) -> None:
    """Change the bridge's fixture mode and wait until the change is live."""
    config = lam.get_function_configuration(FunctionName="openrx-bridge")
    variables = {**config["Environment"]["Variables"], "OPENRX_FIXTURE_MODE": mode}
    lam.update_function_configuration(
        FunctionName="openrx-bridge", Environment={"Variables": variables}
    )
    for _ in range(60):
        status = lam.get_function_configuration(FunctionName="openrx-bridge").get(
            "LastUpdateStatus"
        )
        if status in (None, "Successful"):
            return
        time.sleep(0.5)
    raise TimeoutError("bridge configuration did not update")


def measure(lam: Any, function: str, event: dict[str, Any], n: int) -> tuple[list, list]:
    """N warm calls (after 5 warm-up calls): round trips and the runner's duration_ms."""
    trips, inside = [], []
    for i in range(n + 5):
        ms, answer = invoke(lam, function, event)
        assert answer.get("error") is None, answer
        if i >= 5:
            trips.append(ms)
            inside.append(answer["duration_ms"])
    return trips, inside


def percentiles(values: list[float]) -> str:
    """p50 / p95 / p99 of `values`, in ms."""
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    p50, p95, p99 = cuts[49], cuts[94], cuts[98]
    return f"p50 {p50:7.2f}   p95 {p95:7.2f}   p99 {p99:7.2f}"


def main() -> None:
    """Run the benchmarks and print the results."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", type=int, default=100, help="warm calls per case (default 100)")
    n = parser.parse_args().n
    lam = boto3.client("lambda", **FLOCI)

    print(f"runners, n={n} warm calls per case, ms")
    for label, event in [
        ("sandbox overhead, type check on ", {"code": "1"}),
        ("sandbox overhead, type check off", {"code": "1", "type_check": False}),
    ]:
        trips, inside = measure(lam, "openrx-search-runner", event, n)
        print(f"  {label}  round trip {percentiles(trips)}")
        print(f"  {' ' * len(label)}  in runner  {percentiles(inside)}")

    try:
        set_bridge_mode(lam, "record")
        _, recorded = invoke(lam, "openrx-execute-runner", {"code": FIVE_REQUESTS})
        assert recorded.get("error") is None, recorded
        set_bridge_mode(lam, "replay")
        trips, inside = measure(lam, "openrx-execute-runner", {"code": FIVE_REQUESTS}, n)
    finally:
        set_bridge_mode(lam, "live")
    print(f"  execute, 5 requests (replay)       round trip {percentiles(trips)}")
    print(f"                                     in runner  {percentiles(inside)}")


if __name__ == "__main__":
    main()

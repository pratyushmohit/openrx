"""Measure the deployed bridge's overhead (docs/slos.md, README §6.4).

Run with `make bench-bridge` (needs `make up && make deploy`). It records one fixture,
switches the bridge to replay mode so public API latency doesn't count, sends N warm
calls, prints percentiles, and puts the bridge back in live mode.

Two numbers are reported:
- bridge_ms: time inside the bridge (validate, build, S3 fixture read), as the bridge
  reports it. This is the "bridge request() overhead" SLI.
- round trip: host -> Floci -> bridge -> host. It adds the invoke hop from this
  machine, which the runner (inside Docker) pays less of (E4: 4.2 ms p50).
"""

import argparse
import json
import statistics
import time
import uuid
from typing import Any

import boto3

FLOCI = {
    "endpoint_url": "http://127.0.0.1:4566",
    "region_name": "us-east-1",
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
}
FUNCTION = "openrx-bridge"
REQUEST = {"source": "rxnorm", "path": "/rxcui.json", "params": {"name": "metformin"}}


def set_mode(lam: Any, mode: str) -> None:
    """Change the bridge's fixture mode and wait until the change is live."""
    config = lam.get_function_configuration(FunctionName=FUNCTION)
    variables = {**config["Environment"]["Variables"], "OPENRX_FIXTURE_MODE": mode}
    lam.update_function_configuration(FunctionName=FUNCTION, Environment={"Variables": variables})
    for _ in range(60):
        if lam.get_function_configuration(FunctionName=FUNCTION).get("LastUpdateStatus") in (
            None,
            "Successful",
        ):
            return
        time.sleep(0.5)
    raise TimeoutError("function configuration did not update")


def invoke(lam: Any, execution_id: str) -> tuple[float, dict[str, Any]]:
    """One call; returns (round-trip ms, answer)."""
    started = time.perf_counter()
    response = lam.invoke(
        FunctionName=FUNCTION, Payload=json.dumps({**REQUEST, "execution_id": execution_id})
    )
    answer = json.loads(response["Payload"].read())
    return (time.perf_counter() - started) * 1000, answer


def percentiles(values: list[float]) -> str:
    """p50 / p95 / p99 of `values`, in ms."""
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    p50, p95, p99 = cuts[49], cuts[94], cuts[98]
    return f"p50 {p50:6.2f}   p95 {p95:6.2f}   p99 {p99:6.2f}   max {max(values):6.2f}"


def main() -> None:
    """Run the benchmark and print the results."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", type=int, default=200, help="warm calls to time (default 200)")
    n = parser.parse_args().n

    lam = boto3.client("lambda", **FLOCI)
    try:
        set_mode(lam, "record")
        _, recorded = invoke(lam, "bench-record")
        assert recorded["ok"], recorded
        set_mode(lam, "replay")

        round_trips, inside = [], []
        # The budget allows 50 calls per execution, so use a fresh id per 50.
        for i in range(n + 5):  # the first 5 warm the container up
            ms, answer = invoke(lam, f"bench-{i // 50}-{uuid.uuid4().hex[:6]}")
            assert answer["ok"] and answer["mode"] == "replay", answer
            if i >= 5:
                round_trips.append(ms)
                inside.append(answer["bridge_ms"])
    finally:
        set_mode(lam, "live")

    print(f"bridge in replay mode, n={n} warm calls, ms")
    print(f"  bridge_ms   (SLI)  {percentiles(inside)}")
    print(f"  round trip (host)  {percentiles(round_trips)}")


if __name__ == "__main__":
    main()

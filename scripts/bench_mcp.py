"""Measure search and execute end to end through the MCP server (docs/slos.md, §6.4).

Run with `make bench-mcp` (needs `make up && make deploy`). Each call goes MCP client
-> OpenRx MCP server -> runner Lambda -> back, over fastmcp's in-memory transport. The
execute case makes 5 request() calls with the bridge in replay mode, so public API
latency doesn't count; the bridge is put back in live mode afterwards.
"""

import argparse
import asyncio
import os
import statistics
import sys
import time
from typing import Any

import boto3
from fastmcp import Client

sys.path.insert(0, os.path.dirname(__file__))
from bench_runners import FIVE_REQUESTS, FLOCI, set_bridge_mode  # noqa: E402

from openrx.mcp_server.server import server_from_environment  # noqa: E402

SEARCH = "[h['id'] for h in catalog_find('adverse events', limit=3)]"


def percentiles(values: list[float]) -> str:
    """p50 / p95 / p99 of `values`, in ms."""
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    p50, p95, p99 = cuts[49], cuts[94], cuts[98]
    return f"p50 {p50:7.2f}   p95 {p95:7.2f}   p99 {p99:7.2f}"


async def measure(client: Client, tool: str, code: str, n: int) -> list[float]:
    """N warm calls (after 5 warm-up calls), in ms."""
    times = []
    for i in range(n + 5):
        started = time.perf_counter()
        result = await client.call_tool(tool, {"code": code})
        elapsed = (time.perf_counter() - started) * 1000
        content: Any = result.structured_content or {}
        assert content.get("error") is None, content
        if i >= 5:
            times.append(elapsed)
    return times


async def run(n: int) -> tuple[list[float], list[float]]:
    """Time search, then execute with the bridge replaying."""
    server = server_from_environment()
    lam = boto3.client("lambda", **FLOCI)
    async with Client(server) as client:
        search = await measure(client, "search", SEARCH, n)
        try:
            set_bridge_mode(lam, "record")
            await client.call_tool("execute", {"code": FIVE_REQUESTS})
            set_bridge_mode(lam, "replay")
            execute = await measure(client, "execute", FIVE_REQUESTS, n)
        finally:
            set_bridge_mode(lam, "live")
    return search, execute


def main() -> None:
    """Run the benchmark and print the results."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", type=int, default=100, help="warm calls per tool (default 100)")
    n = parser.parse_args().n
    # The server reads Floci's address and dummy credentials from the environment.
    os.environ.setdefault("AWS_DEFAULT_REGION", FLOCI["region_name"])
    os.environ.setdefault("AWS_ENDPOINT_URL", FLOCI["endpoint_url"])
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

    search, execute = asyncio.run(run(n))
    print(f"through the MCP server, n={n} warm calls, ms")
    print(f"  search  (catalog_find)              {percentiles(search)}")
    print(f"  execute (5 requests, replay)        {percentiles(execute)}")


if __name__ == "__main__":
    main()

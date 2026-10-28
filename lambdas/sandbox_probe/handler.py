"""TEMP(M3): probe Lambda for the sandbox wrapper (M3 piece 3).

Runs the real Sandbox with the real catalog_* functions and a fake async request().
Replaced by the search-runner and execute-runner Lambdas in pieces 4-5; delete then.
"""

import asyncio
import time

from openrx.catalog.functions import catalog_functions
from openrx.catalog.loader import load_catalog
from openrx.runner.sandbox import Limits, SandboxHost

_HOSTS = {"search": SandboxHost("search"), "execute": SandboxHost("execute")}
_FUNCTIONS = catalog_functions(load_catalog())


async def _fake_request(source, path, params=None):
    if path == "/fail":
        raise ValueError(f"unknown_path: {source} has no endpoint {path!r}")
    await asyncio.sleep(0.2)  # lets gather() show whether calls overlap
    return {
        "status": 200,
        "data": {"source": source, "path": path, "params": params},
        "truncated": False,
        "notes": [],
    }


def handler(event, context):
    """Run event["code"] in the sandbox and return the ToolResult."""
    runner = event.get("runner", "execute")
    functions = dict(_FUNCTIONS)
    if runner == "execute":
        functions["request"] = _fake_request
    started = time.perf_counter()
    result = _HOSTS[runner].run(
        event["code"],
        functions,
        type_check=event.get("type_check", True),
        limits=Limits(**event.get("limits", {})),
    )
    out = result.to_dict()
    out["handler_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return out

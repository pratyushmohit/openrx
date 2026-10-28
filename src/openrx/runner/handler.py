"""The runner Lambdas' handlers: an event with code in, a ToolResult out.

Event (sent by the MCP server, which is trusted; the code inside it is not):

    {"code": "...", "type_check": true, "max_seconds": 60}

`max_seconds` can only lower the run's time limit, never raise it. Every outcome,
including a malformed event, comes back as a ToolResult dict, never a Lambda error,
so the MCP server always has something to show the model.
"""

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Protocol, cast

from openrx.catalog.functions import catalog_functions
from openrx.catalog.loader import load_catalog
from openrx.runner.requests import LambdaBridge, LambdaClient, RequestFunction
from openrx.runner.results import ToolResult
from openrx.runner.sandbox import Limits
from openrx.runner.stubs import Runner as RunnerKind

MAX_CODE_CHARS = 50_000


class Host(Protocol):
    """What the handler needs from SandboxHost (a fake in unit tests)."""

    def run(
        self,
        code: str,
        functions: Mapping[str, Callable[..., Any]],
        type_check: bool = True,
        limits: Limits | None = None,
    ) -> ToolResult:
        """Run code with these functions and return the result."""
        ...


class Runner:
    """One runner Lambda: its sandbox host and the functions its code may call."""

    def __init__(
        self,
        kind: RunnerKind,
        host: Host,
        functions: Mapping[str, Callable[..., Any]],
        limits: Limits | None = None,
        log: Callable[[str], None] = print,
        request_factory: Callable[[], RequestFunction] | None = None,
    ) -> None:
        """Wire a runner of `kind` to its host and functions.

        `request_factory` gives each run its own request() (execute-runner only).
        """
        self.kind: RunnerKind = kind
        self._host = host
        self._functions = dict(functions)
        self._limits = limits or Limits()
        self._log = log
        self._request_factory = request_factory

    def handle(self, event: Any) -> dict[str, Any]:
        """Run the event's code and return the ToolResult as a dict."""
        try:
            code, type_check, limits = self._parse(event)
        except ValueError as e:
            result = ToolResult(error={"type": "BadRequest", "message": str(e)})
        else:
            functions = dict(self._functions)
            request = self._request_factory() if self._request_factory else None
            if request is not None:
                functions["request"] = request
            result = self._host.run(code, functions, type_check=type_check, limits=limits)
            if request is not None:
                result.request_calls = request.calls
                result.bytes_fetched = request.bytes_fetched
                result.notes.extend(n for n in request.notes if n not in result.notes)
        self._log(
            json.dumps(
                {
                    "event": f"{self.kind}_run",
                    "ok": result.error is None,
                    "error_type": (result.error or {}).get("type"),
                    "duration_ms": result.duration_ms,
                    "request_calls": result.request_calls,
                    "bytes_fetched": result.bytes_fetched,
                    "bytes_returned": result.bytes_returned,
                    "truncated": result.truncated,
                }
            )
        )
        return result.to_dict()

    def _parse(self, event: Any) -> tuple[str, bool, Limits]:
        if not isinstance(event, dict):
            raise ValueError("the event must be an object with a 'code' field")
        code = event.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("'code' must be a non-empty string of Python")
        if len(code) > MAX_CODE_CHARS:
            raise ValueError(f"'code' is longer than {MAX_CODE_CHARS:,} characters")
        type_check = event.get("type_check", True)
        if not isinstance(type_check, bool):
            raise ValueError("'type_check' must be true or false")
        limits = self._limits
        if "max_seconds" in event:
            seconds = event["max_seconds"]
            if isinstance(seconds, bool) or not isinstance(seconds, int | float) or seconds <= 0:
                raise ValueError("'max_seconds' must be a positive number")
            limits = replace(limits, seconds=min(float(seconds), limits.seconds))
        return code, type_check, limits


_search: Runner | None = None


def search_runner() -> Runner:
    """The search-runner: catalog_* functions only, no request() (README FR-2)."""
    from openrx.runner.sandbox import SandboxHost  # starts Monty; Lambda only (D-005)

    return Runner("search", SandboxHost("search"), catalog_functions(load_catalog()))


def search_handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point for the search-runner. Built once per container."""
    global _search
    if _search is None:
        _search = search_runner()
    return _search.handle(event)


_execute: Runner | None = None


def execute_runner(env: Mapping[str, str] = os.environ) -> Runner:
    """The execute-runner: catalog_* functions plus request() through the bridge."""
    import boto3  # in the Lambda runtime; on Floci it uses the injected AWS_ENDPOINT_URL
    from botocore.config import Config

    from openrx.runner.sandbox import SandboxHost  # starts Monty; Lambda only (D-005)

    # Retries for a busy bridge are handled (and tested) in LambdaBridge, so boto3's
    # own are off; the read timeout outlasts the bridge's 90 s.
    config = Config(retries={"max_attempts": 1, "mode": "standard"}, read_timeout=100)
    client = cast(LambdaClient, boto3.client("lambda", config=config))
    bridge = LambdaBridge(client, env.get("OPENRX_BRIDGE_FUNCTION", "openrx-bridge"))
    return Runner(
        "execute",
        SandboxHost("execute"),
        catalog_functions(load_catalog()),
        request_factory=lambda: RequestFunction(bridge),
    )


def execute_handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point for the execute-runner. Built once per container."""
    global _execute
    if _execute is None:
        _execute = execute_runner()
    return _execute.handle(event)

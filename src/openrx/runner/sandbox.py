"""Run model-written code in Monty and report what happened as a ToolResult.

This module only ever runs inside the runner Lambdas (D-005), so it is tested there
(tests/integration), not on the host.

Each run gets a fresh Monty session from a pool that lives for the container, so no
state carries from one run to the next. The code's only capabilities are the functions
passed in: Monty has no filesystem, network, environment or imports beyond its own
small standard library unless the host mounts them, and nothing is mounted here.

Limits (docs/design.md §8.4): wall-clock time for the run, memory, recursion depth, and an
outer cap on external calls (Monty's max_suspensions counts every call into the host,
catalog_* included). The 50-request() budget is counted exactly by the runner's own
request() wrapper, with the bridge as a backup (D-012).
"""

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic_monty import (
    AsyncMonty,
    CollectString,
    MontyCrashedError,
    MontyError,
    MontyRuntimeError,
    MontySyntaxError,
    MontyTypingError,
    ResourceLimits,
)

from openrx.runner.results import MAX_STDOUT_CHARS, ToolResult, shape_result
from openrx.runner.stubs import Runner, stubs_for


@dataclass(frozen=True)
class Limits:
    """Per-run limits."""

    seconds: float = 60.0  # docs/design.md §8.4: 60 s wall clock per execute
    memory_bytes: int = 256 * 1024 * 1024
    recursion_depth: int = 200
    external_calls: int = 200  # every host call, catalog_* included
    print_bytes: int = MAX_STDOUT_CHARS * 4  # captured; shape_result cuts what's shown


class Sandbox:
    """Runs code in fresh Monty sessions from one long-lived pool."""

    def __init__(self, pool: AsyncMonty, runner: Runner, limits: Limits | None = None) -> None:
        """Use `pool` (already entered) to run code for `runner`."""
        self._pool = pool
        self._runner: Runner = runner
        self._limits = limits or Limits()

    async def run(
        self,
        code: str,
        functions: Mapping[str, Callable[..., Any]],
        type_check: bool = True,
        clock: Callable[[], float] = time.perf_counter,
    ) -> ToolResult:
        """Type-check and run `code` with `functions` as its only external functions."""
        started = clock()
        stdout = CollectString(max_bytes=self._limits.print_bytes)
        limits: ResourceLimits = {
            "max_feed_duration_secs": self._limits.seconds,
            "max_memory": self._limits.memory_bytes,
            "max_recursion_depth": self._limits.recursion_depth,
            "max_suspensions": self._limits.external_calls,
        }
        try:
            async with self._pool.checkout(
                limits=limits,
                type_check=type_check,
                type_check_stubs=stubs_for(self._runner),
                type_check_format="json",
            ) as session:
                value = await session.feed_run(
                    code, external_lookup=dict(functions), print_callback=stdout
                )
            result = shape_result(value, stdout.output)
        except MontyError as e:
            result = error_result(e, stdout.output)
        result.duration_ms = round((clock() - started) * 1000, 2)
        return result


class SandboxHost:
    """Owns an event loop and a Monty pool for the life of a Lambda container.

    The Lambda handler is synchronous, but AsyncMonty's pool belongs to the event loop
    it was created on: a fresh asyncio.run() per invocation would leave the pool on a
    closed loop. So one loop is kept per container and reused for every run. The pool
    must also be created inside a running loop (AsyncMonty() needs one).
    """

    def __init__(self, runner: Runner) -> None:
        """Start the loop and the pool (one worker: Lambda runs one request at a time)."""
        self._runner: Runner = runner
        self._loop = asyncio.new_event_loop()
        self._pool = self._loop.run_until_complete(self._start_pool())

    @staticmethod
    async def _start_pool() -> AsyncMonty:
        pool = AsyncMonty(min_processes=1, max_processes=1)
        await pool.__aenter__()
        return pool

    def run(
        self,
        code: str,
        functions: Mapping[str, Callable[..., Any]],
        type_check: bool = True,
        limits: Limits | None = None,
    ) -> ToolResult:
        """Run `code` to completion on the container's loop."""
        sandbox = Sandbox(self._pool, self._runner, limits)
        return self._loop.run_until_complete(sandbox.run(code, functions, type_check))


def error_result(error: MontyError, stdout: str) -> ToolResult:
    """Map a Monty error to the error fields of a ToolResult (docs/design.md FR-9)."""
    result = shape_result(None, stdout)
    if isinstance(error, MontyTypingError):
        result.type_errors = parse_type_errors(error.display())
        # The checker sees syntax errors first; call them what they are.
        if result.type_errors and all(
            e.get("code") == "invalid-syntax" for e in result.type_errors
        ):
            result.error = {"type": "SyntaxError", "message": result.type_errors[0]["message"]}
        else:
            result.error = {
                "type": "TypeCheckError",
                "message": "the code has type errors; see type_errors",
            }
    elif isinstance(error, MontySyntaxError):
        result.error = {"type": "SyntaxError", "message": error.display("msg")}
        result.traceback = error.display("traceback")
    elif isinstance(error, MontyRuntimeError):
        inner = error.exception()
        result.error = {"type": type(inner).__name__, "message": error.display("msg")}
        result.traceback = error.display("traceback")
    elif isinstance(error, MontyCrashedError):
        kind = "Timeout" if error.timed_out else "SandboxCrashed"
        result.error = {"type": kind, "message": str(error)}
    else:
        result.error = {"type": type(error).__name__, "message": str(error)}
    return result


def parse_type_errors(rendered: str) -> list[dict[str, Any]]:
    """Turn Monty's JSON type-check output into [{line, column, code, message}].

    Falls back to one entry with the raw text if the output isn't the JSON expected,
    so a format change never hides the errors from the model.
    """
    try:
        diagnostics = json.loads(rendered)
    except ValueError:
        return [{"message": rendered}]
    if isinstance(diagnostics, dict):
        diagnostics = diagnostics.get("diagnostics", [diagnostics])
    if not isinstance(diagnostics, list):
        return [{"message": rendered}]
    errors = []
    for d in diagnostics:
        if not isinstance(d, dict):
            errors.append({"message": str(d)})
            continue
        line, column = _position(d)
        errors.append(
            {
                "line": line,
                "column": column,
                "code": d.get("code") or d.get("check_name"),
                "message": d.get("message") or d.get("description") or str(d),
            }
        )
    return errors


def _position(node: Any, depth: int = 0) -> tuple[int | None, int | None]:
    """The first (line, column) found in a diagnostic, whatever wraps it.

    Checkers nest positions differently ({"location": {"row", "column"}},
    {"range": {"start": {"line", "column"}}}, ...), so search instead of assuming.
    """
    if not isinstance(node, dict) or depth > 4:
        return None, None
    line = node.get("row", node.get("line"))
    if isinstance(line, int):
        column = node.get("column", node.get("col"))
        return line, column if isinstance(column, int) else None
    for key in ("location", "start", "range", "span", "primary_span", "begin"):
        if key in node:
            found = _position(node[key], depth + 1)
            if found[0] is not None:
                return found
    for value in node.values():
        found = _position(value, depth + 1)
        if found[0] is not None:
            return found
    return None, None

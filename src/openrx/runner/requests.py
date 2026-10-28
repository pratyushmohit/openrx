"""request(): how execute-runner code reaches the public APIs, through the bridge.

Each run gets its own RequestFunction with a fresh execution_id, so the bridge's
backup budget and the counters here are per run. The runner's count is the exact
50-call budget (D-012 update); refused calls never reach the bridge.

request() is async (D-014). The bridge is invoked through boto3, which is
synchronous, so each call runs in a worker thread: asyncio.gather() in model code
then really overlaps calls instead of blocking the event loop.

A rejected call raises RequestError("<code>: <message>"), so model code stops with a
traceback at the line that made the call (README FR-9). RequestError is a RuntimeError
because Monty only knows built-in exception types: inside the sandbox a custom class
would show up as a bare Exception. On success it returns
{status, data, truncated, notes}, the Response type in the stubs.
"""

import asyncio
import json
import random
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from botocore.exceptions import ClientError

REQUEST_BUDGET = 50
# Retries for a busy bridge: waits grow 0.1, 0.2, 0.4, ... s, capped at 2 s, for up to
# about 10 s in all, which covers a bridge cold start with calls queued behind it.
THROTTLE_ATTEMPTS = 10
THROTTLE_BACKOFF_SECONDS = 0.1
THROTTLE_MAX_WAIT_SECONDS = 2.0
_THROTTLED = frozenset({"TooManyRequestsException", "ThrottlingException"})


class RequestError(RuntimeError):
    """A request() call that didn't produce an answer. Seen in the sandbox as RuntimeError."""


class LambdaClient(Protocol):
    """The one boto3 Lambda call the bridge client needs."""

    def invoke(self, *, FunctionName: str, Payload: bytes) -> dict[str, Any]:  # noqa: N803
        """Invoke a Lambda synchronously."""
        ...


class Bridge(Protocol):
    """Sends one event to the bridge and returns its answer."""

    def call(self, event: dict[str, Any]) -> dict[str, Any]:
        """The bridge's answer for `event`."""
        ...


class LambdaBridge:
    """The bridge Lambda, invoked with boto3; retries when it is busy.

    The bridge runs as one container (D-012). An invoke that finds it busy can be
    rejected with TooManyRequestsException: on real AWS whenever it is busy, on Floci
    at least while the container is starting. Throttled calls are retried here with
    growing waits. Each wait is jittered (50-100% of its step), so parallel calls from
    one asyncio.gather() spread out instead of colliding again at the same moment.
    """

    def __init__(
        self,
        client: LambdaClient,
        function_name: str,
        attempts: int = THROTTLE_ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0.5, 1.0),
    ) -> None:
        """Invoke `function_name` through `client`."""
        self._client = client
        self._function = function_name
        self._attempts = attempts
        self._sleep = sleep
        self._jitter = jitter

    def call(self, event: dict[str, Any]) -> dict[str, Any]:
        """Invoke the bridge and return its answer."""
        payload = json.dumps(event).encode("utf-8")
        for attempt in range(1, self._attempts + 1):
            try:
                response = self._client.invoke(FunctionName=self._function, Payload=payload)
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code")
                if code in _THROTTLED and attempt < self._attempts:
                    step = THROTTLE_BACKOFF_SECONDS * 2 ** (attempt - 1)
                    self._sleep(min(step, THROTTLE_MAX_WAIT_SECONDS) * self._jitter())
                    continue
                raise RequestError(f"bridge_unavailable: {code}") from None
            body = json.loads(response["Payload"].read())
            if response.get("FunctionError"):
                # The bridge itself crashed; its error text is not for the model.
                raise RequestError("bridge_failed: the bridge could not answer")
            return body
        raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises


class RequestFunction:
    """The request() one run's code sees: counts calls and bytes for that run."""

    def __init__(self, bridge: Bridge, budget: int = REQUEST_BUDGET, max_parallel: int = 1) -> None:
        """A fresh request() with its own execution id and counters.

        `max_parallel` matches how many calls the bridge serves at once (its reserved
        concurrency, D-012). Extra calls from asyncio.gather() wait their turn here:
        sent anyway, they'd be throttled and lose far more time in retry waits (M3:
        5 gathered replay calls took 794 ms that way instead of ~30 ms).
        """
        self.execution_id = f"exe-{uuid.uuid4().hex}"
        self._bridge = bridge
        self._budget = budget
        self._slots = asyncio.Semaphore(max_parallel)
        self.calls = 0  # calls sent to the bridge
        self.bytes_fetched = 0
        self.notes: list[str] = []

    async def __call__(
        self, source: str, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call a catalogued endpoint through the bridge (see the module docstring)."""
        if self.calls >= self._budget:
            raise RequestError(
                f"budget_exceeded: this run has used all {self._budget} request() calls; "
                "combine requests (a larger limit, several ids at once) or filter in code"
            )
        self.calls += 1
        event = {
            "execution_id": self.execution_id,
            "source": source,
            "path": path,
            "params": params,
        }
        loop = asyncio.get_running_loop()
        async with self._slots:
            answer = await loop.run_in_executor(None, self._bridge.call, event)

        if not answer.get("ok"):
            error = answer.get("error") or {}
            raise RequestError(f"{error.get('code', 'rejected')}: {error.get('message', '')}")
        self.bytes_fetched += int(answer.get("bytes") or 0)
        for note in answer.get("notes") or []:
            if note not in self.notes:
                self.notes.append(note)
        return {
            "status": answer["status"],
            "data": answer["data"],
            "truncated": answer["truncated"],
            "notes": answer.get("notes") or [],
        }

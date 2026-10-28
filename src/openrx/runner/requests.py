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
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from botocore.exceptions import ClientError

REQUEST_BUDGET = 50
THROTTLE_ATTEMPTS = 6
THROTTLE_BACKOFF_SECONDS = 0.1  # 0.1, 0.2, 0.4, ... between retries
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

    The bridge runs as one container (D-012). Real AWS rejects a synchronous invoke
    beyond that with TooManyRequestsException rather than queueing it (Floci queues),
    so throttled calls are retried here, with growing waits.
    """

    def __init__(
        self,
        client: LambdaClient,
        function_name: str,
        attempts: int = THROTTLE_ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Invoke `function_name` through `client`."""
        self._client = client
        self._function = function_name
        self._attempts = attempts
        self._sleep = sleep

    def call(self, event: dict[str, Any]) -> dict[str, Any]:
        """Invoke the bridge and return its answer."""
        payload = json.dumps(event).encode("utf-8")
        for attempt in range(1, self._attempts + 1):
            try:
                response = self._client.invoke(FunctionName=self._function, Payload=payload)
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code")
                if code in _THROTTLED and attempt < self._attempts:
                    self._sleep(THROTTLE_BACKOFF_SECONDS * 2 ** (attempt - 1))
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

    def __init__(self, bridge: Bridge, budget: int = REQUEST_BUDGET) -> None:
        """A fresh request() with its own execution id and counters."""
        self.execution_id = f"exe-{uuid.uuid4().hex}"
        self._bridge = bridge
        self._budget = budget
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

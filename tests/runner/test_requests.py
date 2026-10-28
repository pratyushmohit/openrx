import ast
import asyncio
import inspect
import io
import json
import time

import pytest
from botocore.exceptions import ClientError

from openrx.runner.requests import (
    REQUEST_BUDGET,
    LambdaBridge,
    RequestError,
    RequestFunction,
)
from openrx.runner.stubs import stubs_for

OK = {
    "ok": True,
    "status": 200,
    "data": {"idGroup": {"rxnormId": ["6809"]}},
    "bytes": 33,
    "truncated": False,
    "notes": ["secret 'x' has no value set"],
    "remaining_budget": 49,
    "mode": "live",
    "fingerprint": "abc",
}


class FakeBridge:
    def __init__(self, answer=None, delay=0.0):
        self.answer = answer or OK
        self.delay = delay
        self.events = []

    def call(self, event):
        self.events.append(event)
        if self.delay:
            time.sleep(self.delay)
        return self.answer


def run(coro):
    return asyncio.run(coro)


# --- RequestFunction ----------------------------------------------------------------


def test_success_returns_the_response_shape_and_counts():
    bridge = FakeBridge()
    request = RequestFunction(bridge)

    response = run(request("rxnorm", "/rxcui.json", {"name": "metformin"}))

    assert response == {
        "status": 200,
        "data": {"idGroup": {"rxnormId": ["6809"]}},
        "truncated": False,
        "notes": ["secret 'x' has no value set"],
    }
    assert (request.calls, request.bytes_fetched) == (1, 33)
    [event] = bridge.events
    assert event == {
        "execution_id": request.execution_id,
        "source": "rxnorm",
        "path": "/rxcui.json",
        "params": {"name": "metformin"},
    }


def test_each_request_function_has_its_own_execution_id():
    assert RequestFunction(FakeBridge()).execution_id != RequestFunction(FakeBridge()).execution_id
    assert RequestFunction(FakeBridge()).execution_id.startswith("exe-")


def test_rejection_raises_with_the_bridges_code_and_message():
    rejected = {"ok": False, "error": {"code": "unknown_path", "message": "no endpoint '/x'"}}
    request = RequestFunction(FakeBridge(rejected))

    with pytest.raises(RequestError, match=r"^unknown_path: no endpoint '/x'$"):
        run(request("openfda", "/x"))
    assert request.calls == 1 and request.bytes_fetched == 0


def test_budget_refuses_the_call_over_the_limit_without_calling_the_bridge():
    bridge = FakeBridge()
    request = RequestFunction(bridge, budget=2)

    async def three_calls():
        await request("a", "/x")
        await request("a", "/x")
        await request("a", "/x")

    with pytest.raises(RequestError, match="budget_exceeded: this run has used all 2"):
        run(three_calls())
    assert len(bridge.events) == 2 and request.calls == 2


def test_default_budget_is_fifty():
    assert REQUEST_BUDGET == 50


def test_notes_are_collected_once():
    request = RequestFunction(FakeBridge())

    async def twice():
        await request("a", "/x")
        await request("a", "/x")

    run(twice())

    assert request.notes == ["secret 'x' has no value set"]


def test_gathered_requests_run_in_parallel_when_the_bridge_allows():
    # boto3 is synchronous, so each call runs in a thread; three 0.2 s calls overlap.
    request = RequestFunction(FakeBridge(delay=0.2), max_parallel=3)

    async def gathered():
        started = time.perf_counter()
        await asyncio.gather(*(request("a", "/x") for _ in range(3)))
        return time.perf_counter() - started

    assert run(gathered()) < 0.45


def test_calls_beyond_the_bridges_concurrency_wait_their_turn():
    bridge = FakeBridge(delay=0.1)
    request = RequestFunction(bridge, max_parallel=1)

    async def gathered():
        await asyncio.gather(*(request("a", "/x") for _ in range(3)))

    started = time.perf_counter()
    run(gathered())

    assert time.perf_counter() - started >= 0.3  # one at a time
    assert len(bridge.events) == 3 and request.calls == 3


def test_request_matches_its_stub():
    tree = ast.parse(stubs_for("execute"))
    [stub] = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "request"]
    stub_args = [a.arg for a in stub.args.args]
    stub_defaults = [ast.unparse(d) for d in stub.args.defaults]

    real = inspect.signature(RequestFunction.__call__).parameters.values()
    real = [p for p in real if p.name != "self"]

    assert [p.name for p in real] == stub_args
    assert [
        repr(p.default) for p in real if p.default is not inspect.Parameter.empty
    ] == stub_defaults
    assert inspect.iscoroutinefunction(RequestFunction.__call__)


# --- LambdaBridge -------------------------------------------------------------------


class FakeLambda:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def invoke(self, *, FunctionName, Payload):  # noqa: N803 - boto3's names
        self.calls.append((FunctionName, json.loads(Payload)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def throttled():
    return ClientError({"Error": {"Code": "TooManyRequestsException"}}, "Invoke")


def payload(body, function_error=None):
    response = {"Payload": io.BytesIO(json.dumps(body).encode())}
    if function_error:
        response["FunctionError"] = function_error
    return response


def test_bridge_is_invoked_with_the_event():
    client = FakeLambda(payload(OK))

    answer = LambdaBridge(client, "openrx-bridge").call({"source": "rxnorm"})

    assert answer == OK
    assert client.calls == [("openrx-bridge", {"source": "rxnorm"})]


def test_busy_bridge_is_retried_with_growing_waits():
    sleeps = []
    client = FakeLambda(throttled(), throttled(), payload(OK))

    answer = LambdaBridge(client, "b", sleep=sleeps.append, jitter=lambda: 1.0).call({})

    assert answer == OK and sleeps == [0.1, 0.2]


def test_waits_are_capped_and_jittered():
    sleeps = []
    client = FakeLambda(*[throttled()] * 9, payload(OK))

    LambdaBridge(client, "b", sleep=sleeps.append, jitter=lambda: 0.5).call({})

    # 0.1, 0.2, 0.4, 0.8, 1.6, then capped at 2.0; each halved by the jitter.
    assert sleeps == pytest.approx([0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0, 1.0])


def test_default_jitter_stays_between_half_and_full_wait():
    sleeps = []
    client = FakeLambda(*[throttled()] * 5, payload(OK))

    LambdaBridge(client, "b", sleep=sleeps.append).call({})

    steps = [0.1, 0.2, 0.4, 0.8, 1.6]
    assert all(step / 2 <= s <= step for s, step in zip(sleeps, steps, strict=True))


def test_bridge_that_stays_busy_raises():
    client = FakeLambda(*[throttled()] * 3)

    with pytest.raises(RequestError, match="bridge_unavailable: TooManyRequestsException"):
        LambdaBridge(client, "b", attempts=3, sleep=lambda _: None).call({})


def test_other_invoke_errors_are_not_retried():
    client = FakeLambda(ClientError({"Error": {"Code": "AccessDeniedException"}}, "Invoke"))

    with pytest.raises(RequestError, match="bridge_unavailable: AccessDeniedException"):
        LambdaBridge(client, "b", sleep=lambda _: None).call({})
    assert len(client.calls) == 1


def test_crashed_bridge_is_reported_without_its_internals():
    client = FakeLambda(
        payload({"errorMessage": "Traceback ... secret"}, function_error="Unhandled")
    )

    with pytest.raises(RequestError) as excinfo:
        LambdaBridge(client, "b").call({})

    assert str(excinfo.value) == "bridge_failed: the bridge could not answer"

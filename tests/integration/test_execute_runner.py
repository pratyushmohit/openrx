"""The deployed execute-runner on Floci: model-style code in Monty calling the real
bridge, which calls the real public APIs (live mode). Needs `make up && make deploy`;
run with `make test-integration`."""

import json

import boto3
import pytest

pytestmark = pytest.mark.integration

FLOCI = {
    "endpoint_url": "http://127.0.0.1:4566",
    "region_name": "us-east-1",
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
}


@pytest.fixture(scope="module")
def run():
    lam = boto3.client("lambda", **FLOCI)

    def _run(code, **options):
        payload = json.dumps({"code": code, **options}).encode()
        response = lam.invoke(FunctionName="openrx-execute-runner", Payload=payload)
        assert "FunctionError" not in response, response
        return json.loads(response["Payload"].read())

    return _run


def test_code_fetches_through_the_bridge(run):
    code = """
r = await request("rxnorm", "/rxcui.json", {"name": "metformin"})
r["data"]["idGroup"]["rxnormId"]
"""
    answer = run(code)

    assert answer["error"] is None, answer
    assert answer["result"] == ["6809"]
    assert answer["request_calls"] == 1 and answer["bytes_fetched"] > 0


def test_only_the_final_result_comes_back(run):
    # The fan-out pattern: several calls, filtered in code; raw responses never return.
    code = """
import asyncio
names = ["metformin", "aspirin", "warfarin"]
responses = await asyncio.gather(*[request("rxnorm", "/rxcui.json", {"name": n}) for n in names])
{n: r["data"]["idGroup"]["rxnormId"][0] for n, r in zip(names, responses)}
"""
    answer = run(code)

    assert answer["error"] is None, answer
    assert answer["result"] == {"metformin": "6809", "aspirin": "1191", "warfarin": "11289"}
    assert answer["request_calls"] == 3
    assert answer["bytes_fetched"] > answer["bytes_returned"] / 2  # data stayed in the sandbox


def test_rejected_request_stops_the_code_with_a_traceback(run):
    answer = run('await request("openfda", "/drug/events.json")')

    # Monty only knows built-in exception types, so RequestError is a RuntimeError.
    assert answer["error"]["type"] == "RuntimeError"
    assert answer["error"]["message"].startswith("unknown_path:")
    assert "did you mean '/drug/event.json'?" in answer["error"]["message"]
    assert answer["traceback"]


def test_code_can_catch_a_rejection_and_carry_on(run):
    code = """
try:
    await request("openfda", "/nope")
    outcome = "sent"
except Exception as e:
    outcome = str(e).split(":")[0]
outcome
"""
    assert run(code)["result"] == "unknown_path"


def test_type_checker_knows_the_response_shape(run):
    answer = run('r = await request("rxnorm", "/rxcui.json", {"name": "x"})\nr["dat"]')

    assert answer["error"]["type"] == "TypeCheckError"
    assert 'did you mean "data"' in answer["type_errors"][0]["message"]
    assert answer["request_calls"] == 0  # nothing ran


def test_the_51st_request_is_refused_by_the_runner(run):
    code = """
refused = None
for i in range(51):
    try:
        await request("openfda", "/nope")
    except Exception as e:
        if "budget_exceeded" in str(e):
            refused = i
refused
"""
    answer = run(code)

    assert answer["result"] == 50  # calls 0..49 went out; call 50 (the 51st) was refused
    assert answer["request_calls"] == 50


def test_each_run_gets_a_fresh_budget(run):
    answer = run('await request("rxnorm", "/rxcui.json", {"name": "aspirin"})\n1')

    assert answer["error"] is None and answer["request_calls"] == 1

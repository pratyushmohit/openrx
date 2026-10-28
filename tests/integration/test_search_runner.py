"""The deployed search-runner Lambda on Floci: model-style code in Monty with only the
catalog functions. Needs `make up && make deploy`; run with `make test-integration`."""

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
        response = lam.invoke(FunctionName="openrx-search-runner", Payload=payload)
        assert "FunctionError" not in response, response
        return json.loads(response["Payload"].read())

    return _run


def test_search_code_finds_endpoints(run):
    code = """
hits = catalog_find("adverse events", limit=3)
[f"{h['source']}.{h['id']}" for h in hits]
"""
    answer = run(code)

    assert answer["error"] is None
    assert answer["result"][0] == "openfda.drug_event"


def test_search_code_can_read_endpoint_details_and_guides(run):
    code = """
ep = catalog_endpoint("openfda", "drug_event")
guide = catalog_guide("openfda")
{"path": ep["path"], "params": [p["name"] for p in ep["params"]], "spaces": "plain spaces" in guide}
"""
    answer = run(code)

    assert answer["result"] == {
        "path": "/drug/event.json",
        "params": ["search", "count", "sort", "limit", "skip"],
        "spaces": True,
    }


def test_print_output_is_captured(run):
    answer = run("for s in catalog_sources():\n    print(s['id'])\nlen(catalog_sources())")

    assert answer["result"] == 5
    assert answer["stdout"].split() == ["clinicaltrials", "dailymed", "openfda", "pubmed", "rxnorm"]


def test_request_does_not_exist_in_search(run):
    answer = run("await request('rxnorm', '/rxcui.json', {'name': 'x'})")

    assert answer["error"]["type"] == "TypeCheckError"
    assert "request" in answer["type_errors"][0]["message"]


def test_without_type_check_request_is_still_undefined(run):
    answer = run("request('rxnorm', '/rxcui.json')", type_check=False)

    assert answer["error"]["type"] == "NameError"


def test_bad_catalog_call_is_explained(run):
    answer = run("catalog_endpoint('opnfda', 'drug_event')")

    assert answer["error"]["type"] == "ValueError"
    assert "sources: clinicaltrials" in answer["error"]["message"]


def test_each_run_starts_fresh(run):
    run("leftover = 42\nleftover", type_check=False)

    answer = run("leftover", type_check=False)

    assert answer["error"]["type"] == "NameError"


def test_time_limit_stops_runaway_code(run):
    answer = run("while True:\n    pass", max_seconds=1)

    assert answer["error"]["type"] == "TimeoutError"
    assert 1000 <= answer["duration_ms"] < 5000


def test_bad_event_is_a_bad_request(run):
    answer = run("")

    assert answer["error"]["type"] == "BadRequest"

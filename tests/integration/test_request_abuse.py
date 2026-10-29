"""docs/design.md §6.5 request abuse: every attempt must be rejected by the deployed bridge
(M2 exit criterion: 100%). Needs `make up && make deploy`; `make test-integration`.

The cases cover non-catalogued paths, absolute URLs, path traversal (plain and
encoded), reserved parameters, malformed arguments, and budget exhaustion. Rejection
happens before any network call, so none of these reach a public API.
"""

import json
import uuid

import boto3
import pytest

pytestmark = pytest.mark.integration

FLOCI = {
    "endpoint_url": "http://127.0.0.1:4566",
    "region_name": "us-east-1",
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
}

# (description, event without execution_id, expected error code)
ATTACKS = [
    # non-catalogued paths and sources
    ("unknown path", {"source": "openfda", "path": "/drug/secret.json"}, "unknown_path"),
    ("path from another source", {"source": "pubmed", "path": "/drug/event.json"}, "unknown_path"),
    ("unknown source", {"source": "internal", "path": "/admin"}, "unknown_source"),
    (
        "extra segment",
        {"source": "rxnorm", "path": "/rxcui/1/2/related.json", "params": {"tty": "BN"}},
        "unknown_path",
    ),
    # absolute URLs, even to the right host
    ("absolute URL", {"source": "openfda", "path": "https://evil.example/x"}, "absolute_url"),
    (
        "right host, full URL",
        {"source": "openfda", "path": "https://api.fda.gov/drug/event.json"},
        "absolute_url",
    ),
    ("scheme-relative URL", {"source": "openfda", "path": "//evil.example/x"}, "absolute_url"),
    ("file URL", {"source": "openfda", "path": "file:///etc/passwd"}, "absolute_url"),
    (
        "metadata service",
        {"source": "openfda", "path": "http://169.254.169.254/latest/meta-data/"},
        "absolute_url",
    ),
    # traversal
    ("dot-dot", {"source": "openfda", "path": "/drug/../../etc/passwd"}, "bad_path"),
    ("encoded dot-dot", {"source": "openfda", "path": "/drug/%2e%2e/admin"}, "bad_path"),
    ("backslash", {"source": "openfda", "path": "/drug\\..\\admin"}, "bad_path"),
    (
        "traversal in a path param",
        {"source": "rxnorm", "path": "/rxcui/../related.json"},
        "bad_path",
    ),
    ("empty segment", {"source": "openfda", "path": "/drug//event.json"}, "bad_path"),
    ("relative path", {"source": "openfda", "path": "drug/event.json"}, "bad_path"),
    ("query in path", {"source": "openfda", "path": "/drug/event.json?api_key=x"}, "query_in_path"),
    # parameters the bridge owns
    (
        "set the API key",
        {"source": "openfda", "path": "/drug/event.json", "params": {"api_key": "mine"}},
        "reserved_param",
    ),
    (
        "override db",
        {"source": "pubmed", "path": "/esearch.fcgi", "params": {"term": "x", "db": "protein"}},
        "reserved_param",
    ),
    (
        "override email",
        {"source": "pubmed", "path": "/esearch.fcgi", "params": {"term": "x", "email": "a@b.c"}},
        "reserved_param",
    ),
    (
        "override format",
        {"source": "clinicaltrials", "path": "/studies", "params": {"format": "csv"}},
        "reserved_param",
    ),
    (
        "undeclared param",
        {"source": "openfda", "path": "/drug/event.json", "params": {"callback": "x"}},
        "unknown_param",
    ),
    # malformed arguments
    (
        "params not a dict",
        {"source": "openfda", "path": "/drug/event.json", "params": "search=x"},
        "bad_params",
    ),
    (
        "nested value",
        {"source": "openfda", "path": "/drug/event.json", "params": {"search": {"$gt": ""}}},
        "bad_param_value",
    ),
    (
        "huge value",
        {"source": "openfda", "path": "/drug/event.json", "params": {"search": "x" * 5000}},
        "bad_param_value",
    ),
    (
        "too many params",
        {
            "source": "openfda",
            "path": "/drug/event.json",
            "params": {f"p{i}": 1 for i in range(40)},
        },
        "bad_params",
    ),
    ("source not a string", {"source": ["openfda"], "path": "/drug/event.json"}, "bad_source"),
    ("path not a string", {"source": "openfda", "path": None}, "bad_path"),
]


@pytest.fixture(scope="module")
def lam():
    return boto3.client("lambda", **FLOCI)


def invoke(lam, event):
    response = lam.invoke(FunctionName="openrx-bridge", Payload=json.dumps(event).encode())
    assert "FunctionError" not in response, response
    return json.loads(response["Payload"].read())


@pytest.fixture(scope="module")
def results(lam):
    """Send every attack once; shared by the per-case and the summary tests."""
    return {
        name: invoke(lam, {**event, "execution_id": f"abuse-{uuid.uuid4().hex[:12]}"})
        for name, event, _ in ATTACKS
    }


@pytest.mark.parametrize(("name", "event", "code"), ATTACKS, ids=[a[0] for a in ATTACKS])
def test_attack_is_rejected_with_the_right_code(results, name, event, code):
    answer = results[name]

    assert answer["ok"] is False, f"{name} was NOT rejected: {answer}"
    assert answer["error"]["code"] == code


def test_rejection_rate_is_100_percent(results):
    rejected = sum(not a["ok"] for a in results.values())

    assert rejected == len(ATTACKS), f"{rejected}/{len(ATTACKS)} rejected"


def test_budget_exhaustion(lam):
    # The bridge's backup budget (D-012): every call counts, even rejected ones.
    execution_id = f"budget-{uuid.uuid4().hex[:12]}"
    cheap = {"execution_id": execution_id, "source": "openfda", "path": "/nope"}

    answers = [invoke(lam, cheap) for _ in range(50)]
    over = invoke(lam, cheap)

    assert all(a["error"]["code"] == "unknown_path" for a in answers)
    assert over["error"]["code"] == "budget_exceeded"
    assert over["error"]["class"] == "limit"

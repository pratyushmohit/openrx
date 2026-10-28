"""Which Python features model code can use in the runners (README open question 2,
D-015). Runs every snippet in tests/python_features.py in the deployed
execute-runner. Both directions are checked: supported features must work, and
unsupported ones must still fail, so a Monty upgrade that adds one shows up here and
docs/python-subset.md gets updated. Needs `make up && make deploy`."""

import json

import boto3
import pytest
from python_features import FEATURES, NOT_SUPPORTED

pytestmark = pytest.mark.integration

FLOCI = {
    "endpoint_url": "http://127.0.0.1:4566",
    "region_name": "us-east-1",
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
}


@pytest.fixture(scope="module")
def lam():
    return boto3.client("lambda", **FLOCI)


def run(lam, code):
    payload = json.dumps({"code": code}).encode()
    answer = json.loads(
        lam.invoke(FunctionName="openrx-execute-runner", Payload=payload)["Payload"].read()
    )
    return answer


def test_not_supported_names_are_real_features():
    assert set(NOT_SUPPORTED) <= {name for name, _, _ in FEATURES}


@pytest.mark.parametrize(("name", "code", "expected"), FEATURES, ids=[f[0] for f in FEATURES])
def test_feature(lam, name, code, expected):
    answer = run(lam, code)

    if name in NOT_SUPPORTED:
        assert answer["error"] is not None, f"{name} works now: update NOT_SUPPORTED and the docs"
    else:
        assert answer["error"] is None, answer["error"]
        assert answer["result"] == expected

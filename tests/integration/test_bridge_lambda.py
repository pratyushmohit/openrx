"""The deployed bridge Lambda on Floci. Needs `make up && make deploy`; run with
`make test-integration`. These call real public APIs (live mode), so they need the
internet and are kept out of `make check`."""

import json
import time
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
FUNCTION = "openrx-bridge"


@pytest.fixture(scope="module")
def lam():
    return boto3.client("lambda", **FLOCI)


def invoke(lam, **event):
    event.setdefault("execution_id", f"it-{uuid.uuid4().hex[:12]}")
    response = lam.invoke(FunctionName=FUNCTION, Payload=json.dumps(event).encode())
    assert "FunctionError" not in response, response
    return json.loads(response["Payload"].read())


def test_live_request_reaches_the_api(lam):
    answer = invoke(lam, source="rxnorm", path="/rxcui.json", params={"name": "metformin"})

    assert answer["ok"] and answer["status"] == 200
    assert answer["data"]["idGroup"]["rxnormId"] == ["6809"]
    assert answer["mode"] == "live" and answer["remaining_budget"] == 49


def test_attacks_are_rejected_inside_the_lambda(lam):
    answer = invoke(lam, source="openfda", path="https://evil.example/steal")

    assert answer == {
        "ok": False,
        "error": {
            "code": "absolute_url",
            "message": answer["error"]["message"],
            "class": "model_code",
        },
    }


def test_bridge_role_can_read_its_secrets_under_iam_enforcement(lam):
    # The note only appears after a successful GetSecretValue (the secret has no
    # value yet); AccessDenied would give "could not read secret ...".
    answer = invoke(
        lam, source="pubmed", path="/esearch.fcgi", params={"term": "metformin", "retmax": 1}
    )

    assert answer["ok"]
    assert "secret 'openrx/ncbi_api_key' has no value set" in answer["notes"]


def test_fixed_path_is_routed_to_its_own_endpoint(lam):
    # Regression for /studies/enums being matched as /studies/{nctId}.
    answer = invoke(lam, source="clinicaltrials", path="/studies/enums")

    assert answer["ok"] and answer["status"] == 200 and isinstance(answer["data"], list)


# --- record -> replay through S3 ----------------------------------------------------


def set_mode(lam, mode):
    config = lam.get_function_configuration(FunctionName=FUNCTION)
    variables = {**config["Environment"]["Variables"], "OPENRX_FIXTURE_MODE": mode}
    lam.update_function_configuration(FunctionName=FUNCTION, Environment={"Variables": variables})
    for _ in range(60):
        state = lam.get_function_configuration(FunctionName=FUNCTION)
        if state.get("LastUpdateStatus", "Successful") == "Successful":
            return
        time.sleep(0.5)
    raise TimeoutError("function configuration did not update")


@pytest.fixture
def restore_live_mode(lam):
    yield
    set_mode(lam, "live")


def test_record_then_replay_round_trip_through_s3(lam, restore_live_mode):
    s3 = boto3.client("s3", **FLOCI)
    request = {
        "source": "rxnorm",
        "path": "/rxcui.json",
        "params": {"name": f"aspirin-{uuid.uuid4().hex[:6]}"},
    }

    set_mode(lam, "replay")
    missing = invoke(lam, **request)
    assert missing["error"]["code"] == "no_fixture"

    set_mode(lam, "record")
    recorded = invoke(lam, **request)
    key = f"fixtures/rxnorm/{recorded['fingerprint']}.json"
    stored = json.loads(s3.get_object(Bucket="openrx-fixtures", Key=key)["Body"].read())
    assert stored["request"]["params"] == request["params"]

    set_mode(lam, "replay")
    replayed = invoke(lam, **request)
    assert replayed["mode"] == "replay"
    assert (replayed["status"], replayed["data"]) == (recorded["status"], recorded["data"])

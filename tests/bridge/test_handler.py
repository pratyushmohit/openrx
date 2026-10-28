import json

import httpx
import pytest

from openrx.bridge import handler as handler_module
from openrx.bridge.fixtures import Mode
from openrx.bridge.handler import Bridge
from openrx.bridge.limits import CallBudget, RateLimits
from openrx.bridge.secrets import KeyStore
from openrx.bridge.upstream import UpstreamError
from openrx.catalog.loader import load_catalog

CATALOG = load_catalog()
OPENFDA_ARN = "arn:aws:secretsmanager:us-east-1:000000000000:secret:openrx/openfda_api_key-X"


class FakeSecrets:
    def __init__(self, value="secret-key"):
        self.value = value

    def get_secret_value(self, *, SecretId):  # noqa: N803 - boto3's name
        return {"SecretString": self.value}


class MemoryStore:
    def __init__(self):
        self.objects = {}

    def get(self, key):
        return self.objects.get(key)

    def put(self, key, body):
        self.objects[key] = body


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def make_bridge(mode: Mode = "live", answer=None, store=None, config=None, budget=None):
    requests = []

    def upstream(request):
        requests.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer or httpx.Response(200, json={"results": [1]})

    time = FakeTime()
    logs = []
    bridge = Bridge(
        catalog=CATALOG,
        keys=KeyStore(FakeSecrets(), {"openrx/openfda_api_key": OPENFDA_ARN}),
        store=store if store is not None else (MemoryStore() if mode != "live" else None),
        mode=mode,
        config=config or {},
        client=httpx.Client(transport=httpx.MockTransport(upstream)),
        limits=RateLimits(CATALOG, clock=time.clock, sleep=time.sleep),
        budget=budget or CallBudget(),
        log=logs.append,
    )
    return bridge, requests, logs, time


EVENT = {
    "execution_id": "exec-1",
    "source": "openfda",
    "path": "/drug/event.json",
    "params": {"search": "x"},
}


# --- success ----------------------------------------------------------------------


def test_live_request_returns_the_upstream_answer():
    bridge, requests, _, _ = make_bridge()

    answer = bridge.handle(EVENT)

    assert answer["ok"] is True
    assert (answer["status"], answer["data"], answer["mode"]) == (200, {"results": [1]}, "live")
    assert answer["remaining_budget"] == 49
    assert dict(requests[0].url.params) == {"search": "x", "api_key": "secret-key"}


def test_notes_explain_missing_configuration():
    bridge, _, _, _ = make_bridge()

    answer = bridge.handle(
        {"execution_id": "e", "source": "pubmed", "path": "/esearch.fcgi", "params": {"term": "x"}}
    )

    assert "${OPENRX_CONTACT_EMAIL} is not configured; sent without it" in answer["notes"]
    assert any("no ARN configured" in n for n in answer["notes"])


def test_only_openrx_settings_can_fill_placeholders():
    bridge, requests, _, _ = make_bridge(
        config={"OPENRX_CONTACT_EMAIL": "team@example.org", "AWS_SECRET_ACCESS_KEY": "nope"}
    )

    bridge.handle(
        {"execution_id": "e", "source": "pubmed", "path": "/esearch.fcgi", "params": {"term": "x"}}
    )

    assert dict(requests[0].url.params)["email"] == "team@example.org"
    assert bridge._config == {"OPENRX_CONTACT_EMAIL": "team@example.org"}


# --- failures come back structured, with an error class ---------------------------


@pytest.mark.parametrize(
    ("event", "code", "error_class"),
    [
        ({**EVENT, "path": "https://evil.example"}, "absolute_url", "model_code"),
        ({**EVENT, "source": "nope"}, "unknown_source", "model_code"),
        ({**EVENT, "execution_id": None}, "bad_execution_id", "infra"),
        ("not a dict", "bad_execution_id", "infra"),
    ],
)
def test_rejections_are_structured(event, code, error_class):
    bridge, requests, _, _ = make_bridge()

    answer = bridge.handle(event)

    assert isinstance(answer.pop("bridge_ms"), float)
    assert answer == {
        "ok": False,
        "error": {"code": code, "message": answer["error"]["message"], "class": error_class},
    }
    assert requests == []  # nothing was sent


def test_budget_exceeded_is_a_limit_error():
    bridge, _, _, _ = make_bridge(budget=CallBudget(limit=1))
    bridge.handle(EVENT)

    answer = bridge.handle(EVENT)

    assert answer["error"]["code"] == "budget_exceeded" and answer["error"]["class"] == "limit"


def test_upstream_failure_is_an_upstream_error(monkeypatch):
    # Retries and their waits are tested in test_upstream.py; fail at once here.
    def unreachable(*args, **kwargs):
        raise UpstreamError("upstream_unreachable", "openfda drug_event: ConnectError")

    monkeypatch.setattr(handler_module, "send", unreachable)
    bridge, _, _, _ = make_bridge()

    answer = bridge.handle(EVENT)

    assert answer["error"]["code"] == "upstream_unreachable"
    assert answer["error"]["class"] == "upstream"


def test_unexpected_errors_are_reported_without_details():
    class BrokenStore(MemoryStore):
        def put(self, key, body):
            raise RuntimeError("secret-key leaked in a message")

    bridge, _, _, _ = make_bridge(mode="record", store=BrokenStore())

    answer = bridge.handle(EVENT)

    assert answer["error"] == {
        "code": "internal_error",
        "message": "the bridge failed: RuntimeError",
        "class": "infra",
    }


# --- record and replay ------------------------------------------------------------


def test_record_then_replay_round_trip():
    store = MemoryStore()
    recorder, _, _, _ = make_bridge(mode="record", store=store)
    recorder.handle(EVENT)
    replayer, requests, _, time = make_bridge(mode="replay", store=store)

    answer = replayer.handle(EVENT)

    assert answer["ok"] and answer["data"] == {"results": [1]} and answer["mode"] == "replay"
    assert requests == []  # replay never calls the API
    assert time.sleeps == []  # nor waits for a rate-limit slot


def test_replay_without_fixture_is_an_infra_error():
    bridge, _, _, _ = make_bridge(mode="replay")

    answer = bridge.handle(EVENT)

    assert answer["error"]["code"] == "no_fixture" and answer["error"]["class"] == "infra"


def test_record_or_replay_needs_a_bucket():
    with pytest.raises(ValueError, match="OPENRX_FIXTURE_BUCKET"):
        Bridge(
            CATALOG,
            KeyStore(FakeSecrets(), {}),
            None,
            "replay",
            {},
            httpx.Client(),
            RateLimits(CATALOG),
            CallBudget(),
        )


# --- logging ----------------------------------------------------------------------


def test_one_json_log_line_per_call_without_the_key():
    bridge, _, logs, _ = make_bridge()

    bridge.handle(EVENT)
    bridge.handle({**EVENT, "path": "/nope"})

    assert len(logs) == 2
    ok, failed = (json.loads(line) for line in logs)
    assert ok["ok"] is True and ok["endpoint"] == "drug_event" and ok["status"] == 200
    assert failed["ok"] is False and failed["error_code"] == "unknown_path"
    assert all("secret-key" not in line for line in logs)


# --- Lambda entry point -------------------------------------------------------------


def test_handler_builds_the_bridge_once_and_reuses_it(monkeypatch):
    built = []

    def fake_build():
        bridge, _, _, _ = make_bridge()
        built.append(bridge)
        return bridge

    monkeypatch.setattr(handler_module, "_bridge", None)
    monkeypatch.setattr(handler_module, "bridge_from_environment", fake_build)

    first = handler_module.handler(EVENT, None)
    second = handler_module.handler(EVENT, None)

    assert first["ok"] and second["ok"] and len(built) == 1


def test_bridge_from_environment_reads_its_settings(monkeypatch):
    pytest.importorskip("boto3")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    env = {
        "OPENRX_FIXTURE_MODE": "replay",
        "OPENRX_FIXTURE_BUCKET": "openrx-fixtures",
        "OPENRX_SECRET_ARNS": json.dumps({"openrx/openfda_api_key": OPENFDA_ARN}),
        "AWS_ENDPOINT_URL": "http://floci:4566",
    }

    bridge = handler_module.bridge_from_environment(env)

    assert bridge._mode == "replay" and bridge._store is not None

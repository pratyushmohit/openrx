import io
import json
from typing import Any

import pytest
from botocore.exceptions import ClientError

from openrx.bridge.build import build_request
from openrx.bridge.fixtures import (
    FixtureError,
    S3FixtureStore,
    fixture_key,
    parse_mode,
    respond,
)
from openrx.bridge.upstream import UpstreamResult
from openrx.bridge.validate import validate_request
from openrx.catalog.loader import load_catalog

CATALOG = load_catalog()
EMAIL = {"OPENRX_CONTACT_EMAIL": "team@example.org"}
REQ = validate_request(CATALOG, "pubmed", "/esearch.fcgi", {"term": "semaglutide"})
OUT = build_request(REQ, EMAIL, api_key="secret-key")


class MemoryStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def put(self, key: str, body: bytes) -> None:
        self.objects[key] = body


class Upstream:
    """Stands in for upstream.send(); counts calls."""

    def __init__(self, status=200, data=None):
        self.result = UpstreamResult(status, data or {"count": "3"}, 15, False, 1, 120.0)
        self.calls = 0

    def __call__(self) -> UpstreamResult:
        self.calls += 1
        return self.result


# --- modes ----------------------------------------------------------------------


def test_live_calls_upstream_and_records_nothing():
    store, upstream = MemoryStore(), Upstream()

    result = respond("live", REQ, OUT, store, upstream)

    assert result is upstream.result and upstream.calls == 1
    assert store.objects == {}


def test_record_saves_the_answer_under_the_fingerprint():
    store, upstream = MemoryStore(), Upstream()

    respond("record", REQ, OUT, store, upstream)

    key = f"fixtures/pubmed/{OUT.fingerprint}.json"
    assert list(store.objects) == [key] == [fixture_key(OUT)]
    saved = json.loads(store.objects[key])
    assert saved["response"]["status"] == 200 and saved["response"]["data"] == {"count": "3"}
    assert saved["request"]["params"] == {"term": "semaglutide"}


def test_fixture_never_holds_the_key_or_the_contact_email():
    store = MemoryStore()

    respond("record", REQ, OUT, store, Upstream())

    text = next(iter(store.objects.values())).decode("utf-8")
    assert "secret-key" not in text and "team@example.org" not in text
    assert json.loads(text)["request"]["fixed"]["email"] == "${OPENRX_CONTACT_EMAIL}"


def test_record_keeps_real_error_answers():
    store = MemoryStore()

    respond("record", REQ, OUT, store, Upstream(status=404, data={"error": "No matches"}))

    assert len(store.objects) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_record_skips_temporary_failures(status):
    store = MemoryStore()

    respond("record", REQ, OUT, store, Upstream(status=status))

    assert store.objects == {}


def test_replay_answers_from_the_fixture_without_calling_upstream():
    store = MemoryStore()
    respond("record", REQ, OUT, store, Upstream(data={"count": "286"}))
    upstream = Upstream()

    result = respond("replay", REQ, OUT, store, upstream)

    assert upstream.calls == 0
    assert (result.status, result.data, result.attempts) == (200, {"count": "286"}, 0)


def test_replay_matches_a_recording_made_with_other_key_and_email():
    store = MemoryStore()
    respond("record", REQ, OUT, store, Upstream())
    other = build_request(REQ, {"OPENRX_CONTACT_EMAIL": "x@example.org"}, api_key=None)

    assert respond("replay", REQ, other, store, Upstream()).status == 200


def test_replay_without_a_fixture_is_an_error_never_a_live_call():
    upstream = Upstream()

    with pytest.raises(FixtureError) as excinfo:
        respond("replay", REQ, OUT, MemoryStore(), upstream)

    assert excinfo.value.code == "no_fixture"
    assert "OPENRX_FIXTURE_MODE=record" in excinfo.value.message
    assert upstream.calls == 0


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[1, 2]", b'{"version": 1}', b'{"version": 1, "response": {"status": "x"}}'],
)
def test_unreadable_fixture_is_reported(body):
    store = MemoryStore()
    store.objects[fixture_key(OUT)] = body

    with pytest.raises(FixtureError) as excinfo:
        respond("replay", REQ, OUT, store, Upstream())

    assert excinfo.value.code == "bad_fixture"


def test_fixture_from_another_format_version_is_reported():
    store = MemoryStore()
    respond("record", REQ, OUT, store, Upstream())
    key = fixture_key(OUT)
    record = json.loads(store.objects[key])
    record["version"] = 99
    store.objects[key] = json.dumps(record).encode()

    with pytest.raises(FixtureError, match="version 99"):
        respond("replay", REQ, OUT, store, Upstream())


# --- mode setting ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "mode"), [(None, "live"), ("", "live"), ("REPLAY", "replay"), (" record ", "record")]
)
def test_parse_mode(raw, mode):
    assert parse_mode(raw) == mode


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="live, record, replay, replay_or_record"):
        parse_mode("offline")


# --- S3 store -------------------------------------------------------------------


class FakeS3:
    def __init__(self, error_code: str | None = None):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.error_code = error_code

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if self.error_code:
            raise ClientError({"Error": {"Code": self.error_code}}, "GetObject")
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Bucket, Key])}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:  # noqa: N803
        assert ContentType == "application/json"
        self.objects[Bucket, Key] = Body


def test_s3_store_round_trip():
    s3 = FakeS3()
    store = S3FixtureStore(s3, "openrx-fixtures")

    store.put("fixtures/a.json", b"{}")

    assert store.get("fixtures/a.json") == b"{}"
    assert list(s3.objects) == [("openrx-fixtures", "fixtures/a.json")]


def test_s3_store_missing_object_is_none():
    assert S3FixtureStore(FakeS3(), "b").get("fixtures/none.json") is None


def test_s3_store_other_errors_are_raised():
    store = S3FixtureStore(FakeS3(error_code="AccessDenied"), "b")

    with pytest.raises(ClientError):
        store.get("fixtures/x.json")


# --- replay_or_record (evals, M5) -----------------------------------------------


def test_replay_or_record_records_a_miss_then_replays_it():
    store, upstream = MemoryStore(), Upstream(data={"count": "9"})

    first = respond("replay_or_record", REQ, OUT, store, upstream)
    second = respond("replay_or_record", REQ, OUT, store, upstream)

    assert upstream.calls == 1  # the second answer came from the fixture
    assert first.data == second.data == {"count": "9"}
    assert second.attempts == 0 and len(store.objects) == 1


def test_replay_or_record_does_not_freeze_temporary_failures():
    store = MemoryStore()

    respond("replay_or_record", REQ, OUT, store, Upstream(status=503))

    assert store.objects == {}


def test_parse_mode_accepts_replay_or_record():
    assert parse_mode("replay_or_record") == "replay_or_record"

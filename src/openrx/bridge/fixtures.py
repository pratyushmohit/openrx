"""Record and replay upstream answers for reproducible evals (docs/design.md FR-11, D-013).

Modes, set by OPENRX_FIXTURE_MODE:

    live     call the API (default)
    record   call the API and save its answer to S3
    replay   answer from S3 only; a missing fixture is an error, never a live call
    replay_or_record
             replay when a fixture exists, otherwise call the API and save it; for
             evals, where the agent's requests vary from run to run (M5 decision)

Fixtures are stored at fixtures/<source>/<fingerprint>.json (see build.fingerprint).
Every real answer is recorded, whatever its status, because tasks can depend on a 404
"no matches". Temporary failures (429/5xx still failing after retries) are not, since
replaying them would freeze an outage into the eval data. Fixture files never hold
the API key or filled-in placeholder values such as the contact email.
"""

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from botocore.exceptions import ClientError

from openrx.bridge.build import OutgoingRequest
from openrx.bridge.upstream import RETRY_STATUSES, UpstreamResult
from openrx.bridge.validate import ValidRequest

Mode = Literal["live", "record", "replay", "replay_or_record"]
MODES: tuple[Mode, ...] = ("live", "record", "replay", "replay_or_record")
FIXTURE_VERSION = 1


class FixtureError(Exception):
    """Replay could not answer. `code` is for metrics."""

    def __init__(self, code: str, message: str) -> None:
        """Create the error with a short machine-readable code and a message."""
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class FixtureStore(Protocol):
    """Where fixtures are kept: bytes by key."""

    def get(self, key: str) -> bytes | None:
        """The fixture's bytes, or None if there is none."""
        ...

    def put(self, key: str, body: bytes) -> None:
        """Save a fixture, replacing any older one."""
        ...


class S3Client(Protocol):
    """The two boto3 S3 calls the store needs."""

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803 - boto3
        """Read an object."""
        ...

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> Any:  # noqa: N803
        """Write an object."""
        ...


class S3FixtureStore:
    """Fixtures in an S3 bucket (on Floci locally)."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        """Store fixtures in `bucket` through `client`."""
        self._client = client
        self._bucket = bucket

    def get(self, key: str) -> bytes | None:
        """The fixture's bytes, or None if the object doesn't exist."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        return response["Body"].read()

    def put(self, key: str, body: bytes) -> None:
        """Write the fixture as a JSON object."""
        self._client.put_object(
            Bucket=self._bucket, Key=key, Body=body, ContentType="application/json"
        )


def parse_mode(raw: str | None) -> Mode:
    """Read OPENRX_FIXTURE_MODE; unset means live.

    Raises:
        ValueError: for anything other than live, record or replay.
    """
    value = (raw or "live").strip().lower()
    for mode in MODES:
        if value == mode:
            return mode
    raise ValueError(f"OPENRX_FIXTURE_MODE must be one of {', '.join(MODES)}, got {raw!r}")


def fixture_key(out: OutgoingRequest) -> str:
    """The S3 key a request's fixture is stored under."""
    return f"fixtures/{out.source_id}/{out.fingerprint}.json"


def respond(
    mode: Mode,
    req: ValidRequest,
    out: OutgoingRequest,
    store: FixtureStore,
    call_upstream: Callable[[], UpstreamResult],
    clock: Callable[[], float] = time.perf_counter,
) -> UpstreamResult:
    """Answer a request as the mode says.

    Raises:
        FixtureError: in replay mode, if there is no usable fixture.
        UpstreamError: from call_upstream, in live and record modes.
    """
    if mode == "replay":
        return _replay(out, store, clock)
    if mode == "replay_or_record" and store.get(fixture_key(out)) is not None:
        return _replay(out, store, clock)
    result = call_upstream()
    if mode in ("record", "replay_or_record") and result.status not in RETRY_STATUSES:
        store.put(fixture_key(out), _encode(req, out, result))
    return result


def _encode(req: ValidRequest, out: OutgoingRequest, result: UpstreamResult) -> bytes:
    record = {
        "version": FIXTURE_VERSION,
        "request": {
            "source": out.source_id,
            "endpoint": out.endpoint_id,
            "url": out.url,
            "params": req.params,
            # As written in the catalog: placeholders unfilled, no key.
            "fixed": req.source.fixed_params_for(req.endpoint),
        },
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "response": {
            "status": result.status,
            "data": result.data,
            "bytes": result.bytes,
            "truncated": result.truncated,
            "note": result.note,
        },
    }
    return json.dumps(record, ensure_ascii=False, indent=1).encode("utf-8")


def _replay(
    out: OutgoingRequest, store: FixtureStore, clock: Callable[[], float]
) -> UpstreamResult:
    started = clock()
    key = fixture_key(out)
    body = store.get(key)
    if body is None:
        raise FixtureError(
            "no_fixture",
            f"replay mode has no recorded answer for {out.source_id} {out.endpoint_id} "
            f"({key}); run it once with OPENRX_FIXTURE_MODE=record",
        )
    try:
        record = json.loads(body)
        response = record["response"]
        result = UpstreamResult(
            status=int(response["status"]),
            data=response["data"],
            bytes=int(response["bytes"]),
            truncated=bool(response["truncated"]),
            attempts=0,  # nothing was sent
            elapsed_ms=(clock() - started) * 1000,
            note=response.get("note"),
        )
    except (ValueError, KeyError, TypeError) as e:
        raise FixtureError("bad_fixture", f"{key} can't be read: {type(e).__name__}") from None
    if record.get("version") != FIXTURE_VERSION:
        raise FixtureError("bad_fixture", f"{key} has version {record.get('version')!r}")
    return result

"""The bridge Lambda: one request() call in, one structured answer out (README §8.6).

Each call goes through the same steps, in order:

    budget -> validate -> build (+ API key) -> rate limit -> live / record / replay

Every failure comes back as {"ok": false, "error": {...}} with a code and an error
class (README §10: model_code, limit, upstream, infra), never as a Lambda crash, so the
runner can hand it to the model to fix and retry (FR-9). One JSON log line is printed
per call; it never contains the API key.

Configuration (environment, set by Terraform):

    OPENRX_FIXTURE_MODE     live | record | replay | replay_or_record (default live)
    OPENRX_FIXTURE_BUCKET   S3 bucket for fixtures (needed for record and replay)
    OPENRX_SECRET_ARNS      JSON: catalog secret name -> Secrets Manager ARN
    OPENRX_CONTACT_EMAIL    fills ${OPENRX_CONTACT_EMAIL} in the catalog (optional)
"""

import json
import os
import time
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx

from openrx.bridge.build import build_request
from openrx.bridge.fixtures import FixtureError, FixtureStore, Mode, parse_mode, respond
from openrx.bridge.limits import BudgetExceeded, CallBudget, RateLimits
from openrx.bridge.secrets import KeyStore, KeyUnavailable, SecretsClient, parse_secret_arns
from openrx.bridge.upstream import DEFAULT_TIMEOUT, UpstreamError, send
from openrx.bridge.validate import RequestRejected, validate_request
from openrx.catalog.loader import Catalog, load_catalog

# Only these environment variables may fill ${NAME} placeholders, so a catalog entry
# can never pull e.g. AWS credentials from the environment into a request.
_CONFIG_PREFIX = "OPENRX_"


class Bridge:
    """Answers request() calls. Long-lived: one per Lambda container."""

    def __init__(
        self,
        catalog: Catalog,
        keys: KeyStore,
        store: FixtureStore | None,
        mode: Mode,
        config: Mapping[str, str],
        client: httpx.Client,
        limits: RateLimits,
        budget: CallBudget,
        log: Callable[[str], None] = print,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Wire the bridge's parts together."""
        if mode != "live" and store is None:
            raise ValueError(f"fixture mode {mode!r} needs OPENRX_FIXTURE_BUCKET")
        self._catalog = catalog
        self._keys = keys
        self._store = store
        self._mode: Mode = mode
        self._config = {k: v for k, v in config.items() if k.startswith(_CONFIG_PREFIX)}
        self._client = client
        self._limits = limits
        self._budget = budget
        self._log = log
        self._clock = clock

    def handle(self, event: Any) -> dict[str, Any]:
        """Answer one request() call."""
        started = self._clock()
        event = event if isinstance(event, dict) else {}
        log: dict[str, Any] = {
            "event": "bridge_request",
            "execution_id": event.get("execution_id"),
            "source": event.get("source"),
            "mode": self._mode,
        }
        try:
            answer = self._answer(event, log)
        except (RequestRejected, BudgetExceeded, KeyUnavailable, UpstreamError, FixtureError) as e:
            answer = _error(e.code, e.message)
        except Exception as e:  # noqa: BLE001 - never crash the Lambda; report and log
            # Only the type name: a message could contain a URL with the key.
            answer = _error("internal_error", f"the bridge failed: {type(e).__name__}")
        log["ok"] = answer["ok"]
        if not answer["ok"]:
            log["error_code"] = answer["error"]["code"]
            log["error_class"] = answer["error"]["class"]
        # Time spent inside the bridge, upstream included; the SLO measures it in
        # replay mode, where "upstream" is one S3 read (docs/slos.md).
        answer["bridge_ms"] = log["total_ms"] = round((self._clock() - started) * 1000, 2)
        self._log(json.dumps(log, default=str))
        return answer

    def _answer(self, event: dict[str, Any], log: dict[str, Any]) -> dict[str, Any]:
        remaining = self._budget.charge(event.get("execution_id"))
        req = validate_request(
            self._catalog, event.get("source"), event.get("path"), event.get("params")
        )
        log["endpoint"] = req.endpoint.id

        lookup = self._keys.key_for(req.source)
        out = build_request(req, self._config, api_key=lookup.key)
        notes = [*out.notes, *([lookup.note] if lookup.note else [])]

        waited = 0.0
        if self._mode != "replay":  # replay never reaches the API, so no rate limit
            waited = self._limits.acquire(req.source.id)

        def call_upstream() -> Any:
            return send(out, self._client, response_format=req.endpoint.response_format)

        store = self._store
        if store is None:  # live mode only (checked in __init__)
            result = call_upstream()
        else:
            result = respond(self._mode, req, out, store, call_upstream)

        log.update(
            status=result.status,
            bytes=result.bytes,
            attempts=result.attempts,
            upstream_ms=round(result.elapsed_ms, 2),
            waited_ms=round(waited * 1000, 2),
            truncated=result.truncated,
        )
        if result.note:
            notes.append(result.note)
        return {
            "ok": True,
            "status": result.status,
            "data": result.data,
            "bytes": result.bytes,
            "truncated": result.truncated,
            "notes": notes,
            "remaining_budget": remaining,
            "mode": self._mode,
            "fingerprint": out.fingerprint,
        }


# Error class per code, for the metrics in README §10.
_ERROR_CLASS = {
    "budget_exceeded": "limit",
    "bad_execution_id": "infra",
    "missing_api_key": "infra",
    "upstream_timeout": "upstream",
    "upstream_unreachable": "upstream",
    "no_fixture": "infra",
    "bad_fixture": "infra",
    "internal_error": "infra",
}


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message, "class": _ERROR_CLASS.get(code, "model_code")},
    }


def bridge_from_environment(env: Mapping[str, str] = os.environ) -> Bridge:
    """Build the bridge from the Lambda's environment (see the module docstring)."""
    import boto3  # in the Lambda runtime; imported here so unit tests don't need AWS
    from botocore.config import Config

    from openrx.bridge.fixtures import S3Client, S3FixtureStore

    mode = parse_mode(env.get("OPENRX_FIXTURE_MODE"))
    bucket = env.get("OPENRX_FIXTURE_BUCKET")
    catalog = load_catalog()
    # On Floci, AWS_ENDPOINT_URL is injected into the container and boto3 picks it up.
    # An emulator needs path-style S3 URLs (host/bucket): "bucket.floci" doesn't resolve.
    s3_style = "path" if env.get("AWS_ENDPOINT_URL") else "auto"
    # boto3 clients are untyped; cast them to the small interfaces our code uses.
    s3 = cast(S3Client, boto3.client("s3", config=Config(s3={"addressing_style": s3_style})))
    secrets = cast(SecretsClient, boto3.client("secretsmanager"))
    arns = parse_secret_arns(env.get("OPENRX_SECRET_ARNS"))
    return Bridge(
        catalog=catalog,
        keys=KeyStore(secrets, arns),
        store=S3FixtureStore(s3, bucket) if bucket else None,
        mode=mode,
        config=env,
        client=httpx.Client(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": "openrx-bridge"}),
        limits=RateLimits(catalog),
        budget=CallBudget(),
    )


_bridge: Bridge | None = None


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Lambda entry point. The bridge is built once per container and reused."""
    global _bridge
    if _bridge is None:
        _bridge = bridge_from_environment()
    return _bridge.handle(event)

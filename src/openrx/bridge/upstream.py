"""Send a request to the public API and return one uniform result (docs/design.md §8.6).

- Retries 429 and 5xx answers, and network failures, with growing waits; honours a
  server's Retry-After, up to a cap.
- Never follows redirects: a redirect could point outside the catalog's hosts, and the
  bridge only talks to hosts the catalog names.
- Reads at most `max_bytes`, so one huge answer can't exhaust memory.
- Non-200 answers are results, not exceptions: "no matches" is a 404 on openFDA, and
  the guides tell the model how to read each source's errors. Only failing to get any
  answer at all raises UpstreamError.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from openrx.bridge.build import OutgoingRequest

MAX_BYTES = 5_000_000
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 0.5  # waits 0.5 s, then 1 s, ...
MAX_RETRY_AFTER = 10.0  # never wait longer than this for one retry
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)


class UpstreamError(Exception):
    """No answer could be had from the upstream API. `code` names the failure."""

    def __init__(self, code: str, message: str) -> None:
        """Create an error with a short machine-readable code and a message."""
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class UpstreamResult:
    """What the API answered, in one shape for every source."""

    status: int
    data: Any  # parsed JSON, or text; None if a JSON answer was cut off
    bytes: int  # bytes read; more than max_bytes means the answer was cut off
    truncated: bool
    attempts: int
    elapsed_ms: float
    note: str | None = None


def send(
    out: OutgoingRequest,
    client: httpx.Client,
    response_format: Literal["json", "text"] = "json",
    max_bytes: int = MAX_BYTES,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
) -> UpstreamResult:
    """Send `out`, retrying where it helps.

    Raises:
        UpstreamError: if every attempt failed without an answer (timeout, connection
            error). An answer with an error status is returned, not raised.
    """
    started = clock()
    for attempt in range(1, max_attempts + 1):
        last = attempt == max_attempts
        try:
            status, headers, body, truncated = _fetch(out, client, max_bytes)
        except httpx.TimeoutException as e:
            if last:
                raise UpstreamError("upstream_timeout", _describe(out, e)) from None
            sleep(_backoff(attempt))
            continue
        except httpx.TransportError as e:
            if last:
                raise UpstreamError("upstream_unreachable", _describe(out, e)) from None
            sleep(_backoff(attempt))
            continue

        if status in RETRY_STATUSES and not last:
            sleep(_retry_wait(headers, attempt))
            continue

        data, note = _parse(body, headers, response_format, truncated, max_bytes)
        elapsed = (clock() - started) * 1000
        return UpstreamResult(status, data, len(body), truncated, attempt, elapsed, note)
    raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises


def _fetch(
    out: OutgoingRequest, client: httpx.Client, max_bytes: int
) -> tuple[int, httpx.Headers, bytes, bool]:
    with client.stream("GET", out.url, params=out.params, follow_redirects=False) as response:
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > max_bytes:
                return response.status_code, response.headers, bytes(body), True
        return response.status_code, response.headers, bytes(body), False


def _parse(
    body: bytes,
    headers: httpx.Headers,
    response_format: str,
    truncated: bool,
    max_bytes: int,
) -> tuple[Any, str | None]:
    text = body[:max_bytes].decode(_charset(headers), errors="replace")
    if truncated:
        note = f"response was larger than {max_bytes} bytes and was cut off; narrow the query"
        # Half a JSON document can't be parsed; half a text one is still readable.
        return (None if response_format == "json" else text), note
    if response_format == "text":
        return text, None
    try:
        return json.loads(text), None
    except ValueError:
        # e.g. ClinicalTrials.gov answers a bad parameter with a plain-text 400.
        return text, None


def _charset(headers: httpx.Headers) -> str:
    content_type = headers.get("content-type", "")
    for part in content_type.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip('"')
    return "utf-8"


def _backoff(attempt: int) -> float:
    return BACKOFF_SECONDS * 2 ** (attempt - 1)


def _retry_wait(headers: httpx.Headers, attempt: int) -> float:
    retry_after = headers.get("retry-after", "")
    if retry_after.isdigit():
        return min(float(retry_after), MAX_RETRY_AFTER)
    return _backoff(attempt)


def _describe(out: OutgoingRequest, error: Exception) -> str:
    # Never include the URL with its key; the logged form leaves it out.
    return f"{out.source_id} {out.endpoint_id}: {type(error).__name__} for {out.url_for_log()}"

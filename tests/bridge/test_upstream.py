import httpx
import pytest

from openrx.bridge.build import build_request
from openrx.bridge.upstream import (
    MAX_RETRY_AFTER,
    UpstreamError,
    UpstreamResult,
    send,
)
from openrx.bridge.validate import validate_request
from openrx.catalog.loader import load_catalog

CATALOG = load_catalog()
OUT = build_request(
    validate_request(CATALOG, "openfda", "/drug/event.json", {"search": "x"}), {}, api_key="k"
)


class Server:
    """A fake upstream: answers with the given responses (or exceptions), in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def run(server, **kwargs):
    sleeps = []
    client = httpx.Client(transport=httpx.MockTransport(server))
    result = send(OUT, client, sleep=sleeps.append, **kwargs)
    return result, sleeps


# --- answers --------------------------------------------------------------------


def test_json_answer_is_parsed():
    server = Server(httpx.Response(200, json={"results": [1, 2]}))

    result, sleeps = run(server)

    assert isinstance(result, UpstreamResult)
    assert (result.status, result.data, result.truncated, result.attempts) == (
        200,
        {"results": [1, 2]},
        False,
        1,
    )
    assert result.bytes == len(b'{"results":[1,2]}') and sleeps == []


def test_request_goes_to_the_built_url_with_its_params():
    server = Server(httpx.Response(200, json={}))

    run(server)

    sent = server.requests[0]
    assert str(sent.url).startswith("https://api.fda.gov/drug/event.json?")
    assert dict(sent.url.params) == {"search": "x", "api_key": "k"}


def test_text_endpoint_returns_text():
    server = Server(httpx.Response(200, text="1. A paper.\n\nPMID: 1"))

    result, _ = run(server, response_format="text")

    assert result.data == "1. A paper.\n\nPMID: 1"


def test_error_status_is_a_result_not_an_exception():
    # openFDA's "no matches" is a 404; the guide tells the model to read it as zero results.
    body = {"error": {"code": "NOT_FOUND", "message": "No matches found!"}}

    result, _ = run(Server(httpx.Response(404, json=body)))

    assert (result.status, result.data) == (404, body)


def test_plain_text_error_on_a_json_endpoint_is_kept_as_text():
    result, _ = run(Server(httpx.Response(400, text="Invalid value in parameter `x`")))

    assert result.data == "Invalid value in parameter `x`"


def test_charset_from_the_content_type_is_used():
    body = "café".encode("latin-1")
    headers = {"content-type": "text/plain; charset=latin-1"}

    result, _ = run(
        Server(httpx.Response(200, content=body, headers=headers)), response_format="text"
    )

    assert result.data == "café"


def test_redirects_are_not_followed():
    redirect = httpx.Response(301, headers={"location": "https://evil.example/"})
    server = Server(redirect)

    result, _ = run(server)

    assert result.status == 301
    assert len(server.requests) == 1  # evil.example was never contacted


# --- retries --------------------------------------------------------------------


def test_server_errors_are_retried_with_growing_waits():
    server = Server(httpx.Response(503), httpx.Response(502), httpx.Response(200, json={}))

    result, sleeps = run(server)

    assert (result.status, result.attempts) == (200, 3)
    assert sleeps == [0.5, 1.0]


def test_retry_after_is_honoured_up_to_a_cap():
    server = Server(
        httpx.Response(429, headers={"retry-after": "2"}),
        httpx.Response(429, headers={"retry-after": "3600"}),
        httpx.Response(200, json={}),
    )

    _, sleeps = run(server)

    assert sleeps == [2.0, MAX_RETRY_AFTER]


def test_last_error_answer_is_returned_after_all_attempts():
    server = Server(httpx.Response(429), httpx.Response(429), httpx.Response(429, text="slow down"))

    result, sleeps = run(server)

    assert (result.status, result.data, result.attempts) == (429, "slow down", 3)
    assert len(sleeps) == 2


def test_client_errors_are_not_retried():
    server = Server(httpx.Response(400, text="bad"))

    result, sleeps = run(server)

    assert (result.attempts, sleeps) == (1, [])


def test_network_errors_are_retried_then_reported():
    server = Server(
        httpx.ConnectError("refused"), httpx.ConnectError("refused"), httpx.ConnectError("refused")
    )

    with pytest.raises(UpstreamError) as excinfo:
        run(server)

    assert excinfo.value.code == "upstream_unreachable"
    assert len(server.requests) == 3


def test_timeout_is_reported_without_the_api_key():
    server = Server(*[httpx.ReadTimeout("slow")] * 3)

    with pytest.raises(UpstreamError) as excinfo:
        run(server)

    assert excinfo.value.code == "upstream_timeout"
    assert "api_key" not in str(excinfo.value) and "=k" not in str(excinfo.value)
    assert "openfda drug_event" in str(excinfo.value)


def test_a_network_error_then_success():
    server = Server(httpx.ConnectError("blip"), httpx.Response(200, json={"ok": True}))

    result, sleeps = run(server)

    assert (result.data, result.attempts, sleeps) == ({"ok": True}, 2, [0.5])


# --- size cap -------------------------------------------------------------------


def test_oversized_json_is_cut_off_and_not_parsed():
    server = Server(httpx.Response(200, content=b'{"results": [' + b"1," * 100 + b"1]}"))

    result, _ = run(server, max_bytes=50)

    assert result.truncated and result.data is None
    assert result.bytes > 50
    assert "narrow the query" in (result.note or "")


def test_oversized_text_keeps_the_readable_part():
    server = Server(httpx.Response(200, text="a" * 100))

    result, _ = run(server, response_format="text", max_bytes=10)

    assert result.truncated and result.data == "a" * 10


# --- timing ---------------------------------------------------------------------


def test_elapsed_time_is_measured_across_attempts():
    ticks = iter([0.0, 1.25])
    client = httpx.Client(transport=httpx.MockTransport(Server(httpx.Response(200, json={}))))

    result = send(OUT, client, sleep=lambda _: None, clock=lambda: next(ticks))

    assert result.elapsed_ms == 1250.0

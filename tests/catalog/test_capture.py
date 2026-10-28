import json

import httpx
import pytest

from openrx.catalog.capture import MAX_ITEMS, MAX_TEXT, capture_source, example_request, trim
from openrx.catalog.loader import example_path
from openrx.catalog.schema import Source

SOURCE = Source.model_validate(
    {
        "id": "trials",
        "name": "Trials",
        "base_url": "https://api.example.gov/v2",
        "rate_limit": {"per_second": 4},
        "endpoints": [
            {
                "id": "search",
                "path": "/studies",
                "description": "Search studies.",
                "params": [
                    {"name": "q", "description": "Query."},
                    {"name": "limit", "type": "integer", "description": "Max."},
                ],
                "example_params": {"q": 'drug:"x" AND phase:3', "limit": 2},
            },
            {
                "id": "study",
                "path": "/studies/{nctId}",
                "description": "One study.",
                "params": [{"name": "nctId", "in": "path", "required": True, "description": "Id."}],
                "example_params": {"nctId": "NCT 1/2"},
            },
            {
                "id": "draft",
                "path": "/draft",
                "description": "No example yet.",
                "params": [{"name": "q", "description": "Query."}],
            },
            {"id": "version", "path": "/version", "description": "Takes no parameters."},
        ],
    }
)


def fake_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def ok(request):
    return httpx.Response(
        200, json={"results": [{"n": i} for i in range(5)], "url": str(request.url)}
    )


# --- trim -----------------------------------------------------------------------


def test_trim_keeps_shape_but_shortens_lists_and_strings():
    value = {"results": [{"text": "x" * 500, "tags": list(range(10))}] * 5, "total": 5}

    trimmed = trim(value, max_items=2, max_string=10)

    assert trimmed["total"] == 5
    assert len(trimmed["results"]) == 2
    assert trimmed["results"][0]["text"] == "x" * 10 + "…"
    assert trimmed["results"][0]["tags"] == [0, 1]


def test_trim_leaves_small_values_alone():
    value = {"a": [1, None, True], "b": "short", "c": 1.5}

    assert trim(value, max_items=5) == value


def test_trim_keeps_every_key():
    value = {f"field_{i}": i for i in range(50)}

    assert trim(value) == value


# --- example_request --------------------------------------------------------------


def test_example_request_splits_query_params():
    url, params = example_request(SOURCE, SOURCE.endpoints[0])

    assert url == "https://api.example.gov/v2/studies"
    assert params == {"q": 'drug:"x" AND phase:3', "limit": 2}


def test_example_request_fills_and_escapes_path_params():
    url, params = example_request(SOURCE, SOURCE.endpoints[1])

    assert url == "https://api.example.gov/v2/studies/NCT%201%2F2"  # "/" can't add a segment
    assert params == {}


# --- capture_source -------------------------------------------------------------


def test_capture_writes_a_trimmed_example_with_its_request(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return ok(request)

    results = capture_source(SOURCE, fake_client(handler), root=tmp_path, only="search")

    [result] = results
    assert result.error is None
    raw = example_path(tmp_path, "trials", "search").read_bytes()
    assert b"\r\n" not in raw  # LF on every OS
    record = json.loads(raw)
    assert record["request"] == {
        "url": "https://api.example.gov/v2/studies",
        "params": {"q": 'drug:"x" AND phase:3', "limit": 2},
    }
    assert len(record["response"]["results"]) == MAX_ITEMS
    # Spaces are encoded as "+" by the client, never sent as a literal "+".
    assert seen[0].url.query == b"q=drug%3A%22x%22+AND+phase%3A3&limit=2"


def test_capture_never_sends_an_api_key(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return ok(request)

    capture_source(SOURCE, fake_client(handler), root=tmp_path, sleep=lambda _: None)

    assert all("api_key" not in str(r.url) for r in seen)


def test_failed_request_writes_nothing(tmp_path):
    def handler(request):
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    [result] = capture_source(SOURCE, fake_client(handler), root=tmp_path, only="search")

    assert result.file is None
    assert result.error is not None and result.error.startswith("HTTP 404")
    assert not example_path(tmp_path, "trials", "search").exists()


def test_network_error_is_reported(tmp_path):
    def handler(request):
        raise httpx.ConnectError("no route")

    [result] = capture_source(SOURCE, fake_client(handler), root=tmp_path, only="search")

    assert result.error is not None and "request failed" in result.error


def test_non_json_response_is_reported(tmp_path):
    def handler(request):
        return httpx.Response(200, text="<html>")

    [result] = capture_source(SOURCE, fake_client(handler), root=tmp_path, only="search")

    assert result.error == "response is not JSON"


def test_endpoint_with_params_but_no_example_params_is_skipped(tmp_path):
    [result] = capture_source(SOURCE, fake_client(ok), root=tmp_path, only="draft")

    assert result.error == "no example_params in the YAML"


def test_endpoint_without_params_is_captured_as_is(tmp_path):
    [result] = capture_source(SOURCE, fake_client(ok), root=tmp_path, only="version")

    assert result.error is None
    assert example_path(tmp_path, "trials", "version").exists()


def test_unknown_endpoint_is_reported(tmp_path):
    [result] = capture_source(SOURCE, fake_client(ok), root=tmp_path, only="nope")

    assert result.error == "no endpoint 'nope' in trials"


def test_requests_are_spaced_by_the_rate_limit(tmp_path):
    pauses = []

    results = capture_source(SOURCE, fake_client(ok), root=tmp_path, sleep=pauses.append)

    assert [r.endpoint_id for r in results] == ["search", "study", "draft", "version"]
    assert pauses == pytest.approx([0.25, 0.25, 0.25])  # 4 per second, none before the first


# --- fixed params and text responses ----------------------------------------------

PUBMED = Source.model_validate(
    {
        "id": "pubmed",
        "name": "PubMed",
        "base_url": "https://eutils.example.gov",
        "rate_limit": {"per_second": 3},
        "fixed_params": {"tool": "openrx", "email": "${CONTACT_EMAIL}"},
        "endpoints": [
            {
                "id": "abstracts",
                "path": "/efetch.fcgi",
                "description": "Abstracts.",
                "fixed_params": {"db": "pubmed", "retmode": "text"},
                "response_format": "text",
                "params": [{"name": "id", "required": True, "description": "PMIDs."}],
                "example_params": {"id": "1"},
            }
        ],
    }
)


def test_fixed_params_are_sent_filled_but_recorded_with_placeholders(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text="1. A paper. PMID: 1")

    [result] = capture_source(
        PUBMED, fake_client(handler), root=tmp_path, env={"CONTACT_EMAIL": "me@example.org"}
    )

    assert result.error is None and result.warnings == ()
    sent = dict(seen[0].url.params)
    assert sent == {
        "id": "1",
        "tool": "openrx",
        "email": "me@example.org",
        "db": "pubmed",
        "retmode": "text",
    }
    text = example_path(tmp_path, "pubmed", "abstracts").read_text(encoding="utf-8")
    assert "me@example.org" not in text  # the committed file never holds the real value
    assert json.loads(text)["request"]["params"]["email"] == "${CONTACT_EMAIL}"


def test_missing_placeholder_value_is_a_warning_and_the_param_is_left_out(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text="abstract")

    [result] = capture_source(PUBMED, fake_client(handler), root=tmp_path, env={})

    assert result.error is None
    assert result.warnings == ("${CONTACT_EMAIL} is not set; sent without it",)
    assert "email" not in dict(seen[0].url.params)


def test_text_response_is_stored_as_a_trimmed_string(tmp_path):
    long_text = "word " * 1000

    capture_source(
        PUBMED, fake_client(lambda r: httpx.Response(200, text=long_text)), root=tmp_path, env={}
    )

    record = json.loads(example_path(tmp_path, "pubmed", "abstracts").read_text(encoding="utf-8"))
    assert record["response"] == long_text[:MAX_TEXT] + "…"

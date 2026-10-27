import copy

import pytest
from pydantic import ValidationError

from openrx.catalog.schema import Endpoint, Param, Source

VALID_SOURCE = {
    "id": "clinicaltrials",
    "name": "ClinicalTrials.gov",
    "base_url": "https://clinicaltrials.gov/api/v2",
    "rate_limit": {"per_second": 5},
    "auth": {"type": "query_param", "name": "api_key", "secret": "openrx/ct_key"},
    "guide": "Use query.term for free-text search.",
    "endpoints": [
        {
            "id": "study",
            "path": "/studies/{nctId}",
            "description": "One study by NCT id.",
            "params": [
                {
                    "name": "nctId",
                    "in": "path",
                    "required": True,
                    "description": "NCT id.",
                    "example": "NCT04280705",
                },
                {"name": "format", "description": "json or csv.", "example": "json"},
            ],
            "example_response": {"protocolSection": {}},
        },
        {"id": "version", "path": "/version", "description": "API version."},
    ],
}


def source(**changes):
    """A valid source dict with top-level fields replaced."""
    data = copy.deepcopy(VALID_SOURCE)
    data.update(changes)
    return data


def endpoint(**changes):
    """A valid endpoint dict (the templated one) with fields replaced."""
    data = copy.deepcopy(VALID_SOURCE["endpoints"][0])
    data.update(changes)
    return data


def assert_invalid(model, data, message):
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(data)
    assert message in str(excinfo.value)


# --- valid input -----------------------------------------------------------


def test_valid_source_parses_with_defaults():
    parsed = Source.model_validate(VALID_SOURCE)

    study = parsed.endpoints[0]
    assert study.method == "GET"
    assert study.params[0].location == "path"  # YAML key `in`
    assert study.params[1].location == "query"  # default
    assert study.params[1].type == "string"  # default
    assert parsed.auth is not None and parsed.auth.optional is True


def test_source_without_auth_is_valid():
    assert Source.model_validate(source(auth=None)).auth is None


def test_endpoint_lookup_by_id():
    parsed = Source.model_validate(VALID_SOURCE)

    assert parsed.endpoint("version") is parsed.endpoints[1]
    assert parsed.endpoint("missing") is None


def test_parsed_catalog_is_read_only():
    parsed = Source.model_validate(VALID_SOURCE)

    with pytest.raises(ValidationError, match="frozen"):
        parsed.base_url = "https://evil.example"  # type: ignore[misc]


# --- typos and ids -----------------------------------------------------------


def test_unknown_field_is_rejected_so_typos_are_caught():
    param = {"name": "q", "description": "Query.", "requried": True}
    assert_invalid(Param, param, "requried")


@pytest.mark.parametrize("bad_id", ["OpenFDA", "1source", "drug-event", "", "drug event"])
def test_ids_must_be_lowercase_snake_case(bad_id):
    assert_invalid(Source, source(id=bad_id), "id")


def test_empty_description_is_rejected():
    assert_invalid(Endpoint, endpoint(description=""), "description")


# --- security rules: method, base URL, path -----------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "get"])
def test_only_get_is_allowed(method):
    assert_invalid(Endpoint, endpoint(method=method), "method")


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://clinicaltrials.gov/api/v2", "https URL"),
        ("https://clinicaltrials.gov/api/v2?x=1", "https URL"),
        ("https://user:pass@clinicaltrials.gov", "https URL"),
        ("https://clinicaltrials.gov:8443/api", "https URL"),
        ("https://clinicaltrials.gov/api/v2/", "must not end with '/'"),
    ],
)
def test_base_url_must_be_plain_https(url, message):
    assert_invalid(Source, source(base_url=url), message)


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("studies", "must start with '/'"),
        ("/https://evil.example/x", "not a URL"),
        ("//evil.example/x", "not a URL"),
        ("/studies?format=json", "query string"),
        ("/studies#top", "query string"),
        ("/studies/../admin", "'..'"),
        ("/studies/%2e%2e/admin", "may only contain"),
        (r"/studies\admin", "may only contain"),
        ("/studies/a b", "may only contain"),
    ],
)
def test_path_cannot_be_bent_into_another_url(path, message):
    assert_invalid(Endpoint, endpoint(path=path, params=[]), message)


# --- params vs path placeholders ---------------------------------------------


def test_placeholder_needs_a_path_param():
    assert_invalid(Endpoint, endpoint(params=[]), "without a path parameter: ['nctId']")


def test_path_param_must_appear_in_path():
    assert_invalid(Endpoint, endpoint(path="/studies"), "not used in the path: ['nctId']")


def test_path_param_must_be_required():
    param = {"name": "nctId", "in": "path", "description": "NCT id."}
    assert_invalid(Param, param, "must be required")


def test_empty_placeholder_is_rejected():
    assert_invalid(Endpoint, endpoint(path="/studies/{}"), "invalid placeholder")


def test_duplicate_param_names_are_rejected():
    params = [
        {"name": "q", "description": "One."},
        {"name": "q", "description": "Two."},
    ]
    assert_invalid(Endpoint, endpoint(path="/studies", params=params), "duplicate parameter")


def test_unknown_param_type_is_rejected():
    param = {"name": "q", "description": "Query.", "type": "date"}
    assert_invalid(Param, param, "type")


# --- source-level rules -------------------------------------------------------


def test_duplicate_endpoint_ids_are_rejected():
    dup = [VALID_SOURCE["endpoints"][1], VALID_SOURCE["endpoints"][1]]
    assert_invalid(Source, source(endpoints=dup), "duplicate endpoint ids: ['version']")


def test_source_needs_at_least_one_endpoint():
    assert_invalid(Source, source(endpoints=[]), "endpoints")


@pytest.mark.parametrize("rate", [0, -1])
def test_rate_limit_must_be_positive(rate):
    assert_invalid(Source, source(rate_limit={"per_second": rate}), "per_second")


def test_unknown_auth_type_is_rejected():
    auth = {"type": "header", "name": "X-Key", "secret": "s"}
    assert_invalid(Source, source(auth=auth), "auth")

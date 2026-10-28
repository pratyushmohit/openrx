import pytest

from openrx.bridge.validate import MAX_PARAMS, MAX_VALUE_LENGTH, RequestRejected, validate_request
from openrx.catalog.loader import load_catalog

CATALOG = load_catalog()


def rejected(source, path, params=None):
    with pytest.raises(RequestRejected) as excinfo:
        validate_request(CATALOG, source, path, params)
    return excinfo.value


# --- accepted requests ------------------------------------------------------------


def test_valid_request_is_matched_to_its_endpoint():
    req = validate_request(
        CATALOG, "openfda", "/drug/event.json", {"search": 'generic_name:"metformin"', "limit": 5}
    )

    assert (req.source.id, req.endpoint.id) == ("openfda", "drug_event")
    assert req.params == {"search": 'generic_name:"metformin"', "limit": 5}
    assert req.path_params == {}


def test_path_template_values_are_captured():
    req = validate_request(CATALOG, "rxnorm", "/rxcui/6809/related.json", {"tty": "BN"})

    assert req.endpoint.id == "related"
    assert req.path_params == {"rxcui": "6809"}


def test_no_params_is_fine_when_none_are_required():
    assert validate_request(CATALOG, "clinicaltrials", "/studies/enums").params == {}


@pytest.mark.parametrize(
    ("path", "params", "expected"),
    [
        ("/drug/event.json", {"limit": "5"}, {"limit": 5}),  # integer as text
        ("/studies", {"countTotal": "true"}, {"countTotal": True}),  # boolean as text
        ("/studies", {"countTotal": False}, {"countTotal": False}),
    ],
)
def test_simple_type_spellings_are_coerced(path, params, expected):
    source = "openfda" if path.startswith("/drug") else "clinicaltrials"

    assert validate_request(CATALOG, source, path, params).params == expected


def test_number_for_a_string_param_becomes_text():
    req = validate_request(CATALOG, "pubmed", "/esummary.fcgi", {"id": 38000000})

    assert req.params == {"id": "38000000"}


# --- source and path abuse (README §6.5 request abuse) ----------------------------


def test_unknown_source_suggests_the_closest():
    err = rejected("opnfda", "/drug/event.json")

    assert err.code == "unknown_source"
    assert "did you mean 'openfda'?" in err.message
    assert "sources: clinicaltrials, dailymed, openfda, pubmed, rxnorm" in err.message


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/steal",
        "https://api.fda.gov/drug/event.json",  # even the right host: paths only
        "//evil.example/steal",
        "/redirect?to=https://evil.example",
    ],
)
def test_urls_are_never_accepted(path):
    assert rejected("openfda", path).code in ("absolute_url", "query_in_path")


@pytest.mark.parametrize(
    "path",
    [
        "/drug/../admin",
        "/drug/./event.json",
        "/drug//event.json",
        "/drug/%2e%2e/admin",
        "/drug\\event.json",
        "/drug/event.json/",
        "/drug/event json",
        "drug/event.json",
    ],
)
def test_traversal_and_odd_characters_are_rejected(path):
    assert rejected("openfda", path).code == "bad_path"


def test_path_param_cannot_smuggle_traversal():
    # "{rxcui}" fills exactly one safe segment, so ".." or extra segments can't match.
    assert rejected("rxnorm", "/rxcui/../related.json").code == "bad_path"
    assert rejected("rxnorm", "/rxcui/1/2/related.json").code == "unknown_path"


def test_query_string_in_path_is_rejected():
    err = rejected("openfda", "/drug/event.json?limit=1")

    assert err.code == "query_in_path"
    assert "params" in err.message


def test_unknown_path_suggests_the_closest():
    err = rejected("openfda", "/drug/events.json")

    assert err.code == "unknown_path"
    assert "did you mean '/drug/event.json'?" in err.message
    assert "catalog_endpoints('openfda')" in err.message


def test_path_of_another_source_is_unknown():
    assert rejected("pubmed", "/drug/event.json").code == "unknown_path"


@pytest.mark.parametrize(("source", "path"), [(1, "/x"), ("openfda", None), ("openfda", ["/x"])])
def test_wrong_argument_types(source, path):
    assert rejected(source, path).code in ("bad_source", "bad_path")


# --- parameter abuse --------------------------------------------------------------


def test_api_key_cannot_be_set_by_the_caller():
    err = rejected("openfda", "/drug/event.json", {"api_key": "mine"})

    assert err.code == "reserved_param"
    assert "added automatically" in err.message


@pytest.mark.parametrize(
    ("source", "path", "name"),
    [
        ("pubmed", "/esearch.fcgi", "db"),  # endpoint fixed param (pins db=pubmed)
        ("pubmed", "/esearch.fcgi", "email"),  # source fixed param
        ("clinicaltrials", "/studies", "format"),
    ],
)
def test_fixed_params_cannot_be_overridden(source, path, name):
    params = {"term": "x"} if source == "pubmed" else {}
    params[name] = "other"

    assert rejected(source, path, params).code == "reserved_param"


def test_unknown_param_suggests_the_closest():
    err = rejected("openfda", "/drug/event.json", {"serach": "x"})

    assert err.code == "unknown_param"
    assert "did you mean 'search'?" in err.message


def test_path_param_passed_as_query_param():
    err = rejected("rxnorm", "/rxcui/6809/related.json", {"tty": "BN", "rxcui": "6809"})

    assert err.code == "path_param_in_params"


def test_missing_required_param():
    err = rejected("pubmed", "/esearch.fcgi", {"retmax": 5})

    assert err.code == "missing_param"
    assert "term" in err.message


@pytest.mark.parametrize(
    ("params", "hint"),
    [
        ({"limit": "five"}, "must be a integer"),
        ({"limit": True}, "must be a integer"),  # bool is not an int here
        ({"limit": 2.5}, "must be a integer"),
        ({"search": None}, "must be a string"),
        ({"search": {"nested": 1}}, "must be a string"),
        ({"search": ["a", "b"]}, "join several with '|'"),
        ({"search": "x" * (MAX_VALUE_LENGTH + 1)}, "longer than"),
    ],
)
def test_bad_values_are_rejected(params, hint):
    err = rejected("openfda", "/drug/event.json", params)

    assert err.code == "bad_param_value"
    assert hint in err.message


def test_params_must_be_a_dict_of_named_values():
    assert rejected("openfda", "/drug/event.json", "search=x").code == "bad_params"
    assert rejected("openfda", "/drug/event.json", {1: "x"}).code == "bad_params"


def test_too_many_params():
    params = {f"p{i}": "x" for i in range(MAX_PARAMS + 1)}

    assert rejected("openfda", "/drug/event.json", params).code == "bad_params"


def test_rejection_reads_as_code_and_message():
    err = rejected("openfda", "https://evil.example")

    assert str(err).startswith("absolute_url: ")


def number_catalog():
    from types import MappingProxyType

    from openrx.catalog.loader import Catalog
    from openrx.catalog.schema import Source

    source = Source.model_validate(
        {
            "id": "geo",
            "name": "Geo",
            "base_url": "https://api.example.gov",
            "rate_limit": {"per_second": 1},
            "endpoints": [
                {
                    "id": "near",
                    "path": "/near",
                    "description": "Places near a point.",
                    "params": [{"name": "radius", "type": "number", "description": "Km."}],
                }
            ],
        }
    )
    return Catalog(MappingProxyType({"geo": source}))


@pytest.mark.parametrize(("value", "expected"), [(5, 5), (2.5, 2.5), ("2.5", 2.5), ("3", 3.0)])
def test_number_params_accept_ints_floats_and_their_text(value, expected):
    req = validate_request(number_catalog(), "geo", "/near", {"radius": value})

    assert req.params == {"radius": expected}


@pytest.mark.parametrize("value", [True, "far", "1e3"])
def test_number_params_reject_other_values(value):
    with pytest.raises(RequestRejected, match="must be a number"):
        validate_request(number_catalog(), "geo", "/near", {"radius": value})


@pytest.mark.parametrize(
    ("path", "endpoint"),
    [
        ("/studies/enums", "enums"),  # a fixed path beats the template below
        ("/studies/NCT03548935", "study"),  # /studies/{nctId}
        ("/studies", "search_studies"),
    ],
)
def test_fixed_paths_win_over_templates(path, endpoint):
    # Regression: "/studies/enums" used to match /studies/{nctId} (nctId="enums"),
    # so the study endpoint's fixed format=json was sent and the API refused it.
    assert validate_request(CATALOG, "clinicaltrials", path).endpoint.id == endpoint


def test_no_fixed_path_is_shadowed_by_a_template_in_the_shipped_catalog():
    for source in CATALOG:
        for endpoint in source.endpoints:
            if "{" not in endpoint.path:
                params = dict(endpoint.example_params)  # supplies required params
                matched = validate_request(CATALOG, source.id, endpoint.path, params).endpoint
                assert matched is endpoint, f"{source.id} {endpoint.path} -> {matched.id}"

import copy

import pytest

from openrx.bridge.build import build_request, fingerprint
from openrx.bridge.validate import validate_request
from openrx.catalog.loader import load_catalog

CATALOG = load_catalog()
EMAIL = {"OPENRX_CONTACT_EMAIL": "team@example.org"}


def valid(source, path, params=None):
    return validate_request(CATALOG, source, path, params)


# --- URL and parameters ---------------------------------------------------------


def test_url_is_base_url_plus_concrete_path():
    out = build_request(valid("rxnorm", "/rxcui/6809/related.json", {"tty": "BN"}), {})

    assert out.url == "https://rxnav.nlm.nih.gov/REST/rxcui/6809/related.json"
    assert out.params == {"tty": "BN"}
    assert (out.source_id, out.endpoint_id) == ("rxnorm", "related")


def test_fixed_params_are_merged_and_placeholders_filled():
    out = build_request(valid("pubmed", "/esearch.fcgi", {"term": "x"}), EMAIL)

    assert out.params == {
        "term": "x",
        "tool": "openrx",
        "email": "team@example.org",
        "db": "pubmed",
        "retmode": "json",
    }
    assert out.notes == ()


def test_unconfigured_placeholder_is_left_out_with_a_note():
    out = build_request(valid("pubmed", "/esearch.fcgi", {"term": "x"}), {})

    assert "email" not in out.params
    assert out.notes == ("${OPENRX_CONTACT_EMAIL} is not configured; sent without it",)


def test_api_key_is_added_under_the_sources_auth_param():
    out = build_request(valid("openfda", "/drug/event.json"), {}, api_key="k-123")

    assert out.params == {"api_key": "k-123"}
    assert out.auth_param == "api_key"


def test_no_key_means_no_auth_param():
    out = build_request(valid("openfda", "/drug/event.json"), {}, api_key=None)

    assert "api_key" not in out.params


def test_api_key_is_ignored_for_sources_without_auth():
    out = build_request(valid("rxnorm", "/rxcui.json", {"name": "x"}), {}, api_key="k-123")

    assert out.params == {"name": "x"}


def test_log_url_never_shows_the_key_and_encodes_spaces_as_plus():
    req = valid("openfda", "/drug/event.json", {"search": 'a:"b c" AND d:1'})
    out = build_request(req, {}, api_key="secret-key")

    logged = out.url_for_log()

    assert "secret-key" not in logged
    assert logged == "https://api.fda.gov/drug/event.json?search=a%3A%22b+c%22+AND+d%3A1"


# --- fingerprint ----------------------------------------------------------------


def test_fingerprint_is_stable_and_short():
    req = valid("openfda", "/drug/event.json", {"search": "x", "limit": 5})

    assert fingerprint(req) == fingerprint(req)
    assert len(fingerprint(req)) == 32


def test_fingerprint_ignores_param_order_and_type_spelling():
    a = valid("openfda", "/drug/event.json", {"search": "x", "limit": 5})
    b = valid("openfda", "/drug/event.json", {"limit": "5", "search": "x"})

    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_ignores_api_key_and_contact_email():
    req = valid("pubmed", "/esearch.fcgi", {"term": "x"})

    with_key = build_request(req, EMAIL, api_key="k1")
    other = build_request(req, {"OPENRX_CONTACT_EMAIL": "other@example.org"}, api_key=None)

    assert with_key.fingerprint == other.fingerprint


@pytest.mark.parametrize(
    "other",
    [
        ("openfda", "/drug/event.json", {"search": "y"}),
        ("openfda", "/drug/label.json", {"search": "x"}),
        ("openfda", "/drug/event.json", {"search": "x", "limit": 2}),
    ],
)
def test_different_requests_have_different_fingerprints(other):
    base = valid("openfda", "/drug/event.json", {"search": "x"})

    assert fingerprint(base) != fingerprint(valid(*other))


def test_changing_a_fixed_param_in_the_catalog_changes_the_fingerprint():
    req = valid("pubmed", "/esearch.fcgi", {"term": "x"})
    changed_endpoint = req.endpoint.model_copy(
        update={"fixed_params": {**req.endpoint.fixed_params, "retmode": "xml"}}
    )
    changed = copy.replace(req, endpoint=changed_endpoint)

    assert fingerprint(req) != fingerprint(changed)


# --- the catalog's own examples are valid bridge requests -------------------------


def test_every_catalog_example_passes_the_bridge():
    # If an example request breaks the bridge's rules, the model would learn a call
    # that gets rejected. Path params are filled into the path first.
    for source in CATALOG:
        for endpoint in source.endpoints:
            path = endpoint.path
            query = dict(endpoint.example_params)
            for p in endpoint.params:
                if p.location == "path":
                    path = path.replace(f"{{{p.name}}}", str(query.pop(p.name)))
            validate_request(CATALOG, source.id, path, query)

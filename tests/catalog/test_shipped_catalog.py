"""Checks on the real catalog files in src/openrx/catalog/sources (not test fixtures)."""

import json

from openrx.catalog.loader import CATALOG_DIR, lint_catalog, load_catalog

catalog = load_catalog()


def test_shipped_catalog_loads():
    assert catalog.source("openfda") is not None


def test_openfda_endpoints():
    openfda = catalog.source("openfda")
    assert openfda is not None

    assert [e.id for e in openfda.endpoints] == [
        "drug_event",
        "drug_label",
        "drug_enforcement",
        "drug_ndc",
        "drug_drugsfda",
        "drug_shortages",
    ]
    assert openfda.auth is not None and openfda.auth.secret == "openrx/openfda_api_key"


def test_openfda_endpoints_share_the_query_parameters():
    openfda = catalog.source("openfda")
    assert openfda is not None

    for endpoint in openfda.endpoints:
        assert [p.name for p in endpoint.params] == ["search", "count", "sort", "limit", "skip"]


def test_every_endpoint_has_examples_for_search_and_count():
    # The model learns the query syntax from these examples, so they must exist.
    for source in catalog:
        for endpoint in source.endpoints:
            examples = {p.name: p.example for p in endpoint.params}
            if "search" in examples:
                assert examples["search"], f"{source.id}.{endpoint.id}: no search example"
            if "count" in examples:
                assert examples["count"], f"{source.id}.{endpoint.id}: no count example"


def test_shipped_catalog_passes_lint():
    # Every endpoint has an example file and no example file is orphaned.
    assert lint_catalog() == []


def test_every_endpoint_with_params_has_example_params():
    for source in catalog:
        for endpoint in source.endpoints:
            if endpoint.params:
                assert endpoint.example_params, f"{source.id}.{endpoint.id}: no example_params"


def test_shipped_sources():
    assert [s.id for s in catalog] == ["clinicaltrials", "dailymed", "openfda", "pubmed", "rxnorm"]


def test_pubmed_identifies_itself_without_committing_an_email():
    pubmed = catalog.source("pubmed")
    assert pubmed is not None

    assert pubmed.fixed_params == {"tool": "openrx", "email": "${OPENRX_CONTACT_EMAIL}"}


def test_no_real_email_address_in_any_example_request():
    # Example files are committed; fixed params must be recorded unfilled.
    for path in CATALOG_DIR.glob("examples/*/*.json"):
        params = json.loads(path.read_text(encoding="utf-8"))["request"]["params"]
        for name, value in params.items():
            assert "@" not in str(value), f"{path.name}: {name} looks like a real address"


def test_text_endpoints_have_text_examples():
    for source in catalog:
        for endpoint in source.endpoints:
            if endpoint.response_format == "text":
                assert isinstance(endpoint.example_response, str), f"{source.id}.{endpoint.id}"

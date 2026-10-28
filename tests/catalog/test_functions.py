import json
from types import MappingProxyType

import pytest

from openrx.catalog.functions import (
    SHORT_EXAMPLE_TEXT,
    catalog_endpoint,
    catalog_endpoints,
    catalog_find,
    catalog_functions,
    catalog_guide,
    catalog_sources,
)
from openrx.catalog.loader import Catalog, load_catalog
from openrx.catalog.schema import Source

SHIPPED = load_catalog()


def small_catalog():
    source = Source.model_validate(
        {
            "id": "fda",
            "name": "FDA",
            "base_url": "https://api.example.gov",
            "rate_limit": {"per_second": 1},
            "guide": "Use quotes.",
            "endpoints": [
                {
                    "id": "drug_event",
                    "path": "/drug/event.json",
                    "description": "Adverse event reports.",
                    "params": [{"name": "search", "description": "Filter.", "example": "x"}],
                    "example_params": {"search": "x"},
                    "response_notes": "results is a list.",
                    "example_response": {
                        "results": [{"id": i, "text": "y" * 300} for i in range(5)]
                    },
                },
                {
                    "id": "abstracts",
                    "path": "/efetch",
                    "description": "Paper abstracts as text.",
                    "response_format": "text",
                    "example_response": "word " * 500,
                },
                {"id": "draft", "path": "/draft", "description": "No example yet."},
            ],
        }
    )
    return Catalog(MappingProxyType({"fda": source}))


SMALL = small_catalog()


def is_plain_json(value):
    """Only types that can cross into the Monty sandbox."""
    if isinstance(value, dict):
        return all(isinstance(k, str) and is_plain_json(v) for k, v in value.items())
    if isinstance(value, list):
        return all(is_plain_json(v) for v in value)
    return value is None or isinstance(value, str | int | float | bool)


# --- sources, endpoints, guide ------------------------------------------------------


def test_catalog_sources_lists_ids_names_and_endpoints():
    assert catalog_sources(SMALL) == [
        {
            "id": "fda",
            "name": "FDA",
            "endpoints": ["drug_event", "abstracts", "draft"],
            "has_guide": True,
        }
    ]


def test_catalog_endpoints_for_one_or_all_sources():
    first = {
        "source": "fda",
        "id": "drug_event",
        "path": "/drug/event.json",
        "description": "Adverse event reports.",
    }
    assert catalog_endpoints(SMALL, "fda")[0] == first
    assert len(catalog_endpoints(SHIPPED)) == 23


def test_catalog_guide():
    assert catalog_guide(SMALL, "fda") == "Use quotes."


@pytest.mark.parametrize(
    "call",
    [
        lambda: catalog_endpoints(SMALL, "nope"),
        lambda: catalog_guide(SMALL, "nope"),
        lambda: catalog_endpoint(SMALL, "nope", "drug_event"),
    ],
)
def test_unknown_source_error_lists_the_valid_ones(call):
    with pytest.raises(ValueError, match="no source 'nope'; sources: fda"):
        call()


# --- catalog_endpoint ---------------------------------------------------------------


def test_catalog_endpoint_describes_parameters_and_notes():
    detail = catalog_endpoint(SMALL, "fda", "drug_event")

    assert detail["path"] == "/drug/event.json" and detail["method"] == "GET"
    assert detail["params"] == [
        {
            "name": "search",
            "in": "query",
            "type": "string",
            "required": False,
            "description": "Filter.",
            "example": "x",
        }
    ]
    assert detail["example_params"] == {"search": "x"}
    assert detail["response_notes"] == "results is a list."


def test_example_is_shortened_by_default_but_keeps_every_key():
    detail = catalog_endpoint(SMALL, "fda", "drug_event")

    assert detail["example_shortened"] is True
    [item] = detail["example_response"]["results"]  # one item instead of five
    assert set(item) == {"id", "text"}
    assert len(item["text"]) < 300


def test_full_example_on_request():
    detail = catalog_endpoint(SMALL, "fda", "drug_event", full_example=True)

    assert detail["example_shortened"] is False
    assert len(detail["example_response"]["results"]) == 5


def test_text_example_is_shortened_as_text():
    detail = catalog_endpoint(SMALL, "fda", "abstracts")

    assert detail["response_format"] == "text"
    assert len(detail["example_response"]) == SHORT_EXAMPLE_TEXT + 1  # + "…"


def test_endpoint_without_example():
    detail = catalog_endpoint(SMALL, "fda", "draft")

    assert detail["example_response"] is None and detail["example_shortened"] is False


def test_unknown_endpoint_error_lists_the_valid_ones():
    with pytest.raises(ValueError, match="endpoints: drug_event, abstracts, draft"):
        catalog_endpoint(SMALL, "fda", "nope")


def test_every_shipped_endpoint_is_plain_json_and_reasonably_small():
    for source in SHIPPED:
        for endpoint in source.endpoints:
            detail = catalog_endpoint(SHIPPED, source.id, endpoint.id)
            assert is_plain_json(detail), endpoint.id
            # About 2.5k tokens at most, so a lookup doesn't flood the model's context.
            assert len(json.dumps(detail)) < 10_000, f"{source.id}.{endpoint.id} is too big"


# --- catalog_find -------------------------------------------------------------------


def test_find_returns_summaries_with_scores_best_first():
    results = catalog_find(SMALL, "adverse events")

    assert [r["id"] for r in results] == ["drug_event"]
    assert results[0]["score"] > 0
    assert set(results[0]) == {"source", "id", "path", "description", "score"}


def test_find_ignores_stopwords_and_matches_plurals():
    assert catalog_find(SMALL, "show me the abstracts")[0]["id"] == "abstracts"
    assert catalog_find(SMALL, "abstract")[0]["id"] == "abstracts"


def test_find_with_no_match_returns_an_empty_list():
    assert catalog_find(SMALL, "zebra") == []


def test_find_needs_a_word():
    with pytest.raises(ValueError, match="at least one search word"):
        catalog_find(SMALL, "the of and")


def test_find_respects_the_limit_and_is_deterministic():
    first = catalog_find(SHIPPED, "drug", limit=5)

    assert len(first) == 5
    assert first == catalog_find(SHIPPED, "drug", limit=5)


# A small search-quality eval on the real catalog (README §6.1, search hit rate):
# the right endpoint must be among the first results.
SEARCHES = [
    ("adverse events", "openfda.drug_event", 1),
    ("top side effects of a drug", "openfda.drug_event", 1),
    ("drug recalls", "openfda.drug_enforcement", 1),
    ("drug label boxed warning", "openfda.drug_label", 1),
    ("drug interactions", "openfda.drug_label", 1),
    ("drug shortage", "openfda.drug_shortages", 1),
    ("NDC package code", "openfda.drug_ndc", 1),
    ("approval history", "openfda.drug_drugsfda", 1),
    ("pubmed papers count", "pubmed.search", 1),
    ("abstract of a paper", "pubmed.abstracts", 1),
    ("GLP-1 drugs class", "rxnorm.class_members", 1),
    ("clinical trial results", "clinicaltrials.study", 1),
    ("phase 3 trials sponsor", "clinicaltrials.search_studies", 2),
    ("misspelled drug name", "rxnorm.approximate_match", 2),
    ("brand names of an ingredient", "rxnorm.related", 3),
]


@pytest.mark.parametrize(("query", "expected", "within"), SEARCHES)
def test_search_finds_the_right_endpoint(query, expected, within):
    found = [f"{r['source']}.{r['id']}" for r in catalog_find(SHIPPED, query, limit=within)]

    assert expected in found, f"{query!r} -> {found}"


# --- bound functions for the runner --------------------------------------------------


def test_catalog_functions_are_bound_and_named_as_model_code_sees_them():
    fns = catalog_functions(SMALL)

    assert sorted(fns) == [
        "catalog_endpoint",
        "catalog_endpoints",
        "catalog_find",
        "catalog_guide",
        "catalog_sources",
    ]
    assert fns["catalog_sources"]() == catalog_sources(SMALL)
    assert fns["catalog_endpoints"]("fda") == catalog_endpoints(SMALL, "fda")
    assert fns["catalog_endpoint"]("fda", "draft")["id"] == "draft"
    assert fns["catalog_find"]("abstracts", limit=1)[0]["id"] == "abstracts"
    assert fns["catalog_guide"]("fda") == "Use quotes."

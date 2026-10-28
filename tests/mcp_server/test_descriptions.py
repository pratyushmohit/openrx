import inspect

from openrx.catalog.functions import catalog_functions
from openrx.catalog.loader import load_catalog
from openrx.mcp_server.descriptions import (
    EXECUTE_DESCRIPTION,
    EXECUTE_EXAMPLE,
    INSTRUCTIONS,
    SEARCH_DESCRIPTION,
    SEARCH_EXAMPLE,
)
from openrx.runner.requests import RequestFunction

CATALOG = load_catalog()


def signature_text(name, fn):
    """ "catalog_find(text, limit=10)", as the descriptions write it."""
    parts = []
    for p in inspect.signature(fn).parameters.values():
        if p.name == "self":
            continue
        parts.append(p.name if p.default is inspect.Parameter.empty else f"{p.name}={p.default!r}")
    return f"{name}({', '.join(parts)})"


def test_instructions_name_every_source():
    names = {"openFDA", "ClinicalTrials.gov", "PubMed", "RxNorm", "DailyMed"}

    assert {s.name for s in CATALOG} == names
    assert all(name in INSTRUCTIONS for name in names)


def test_search_description_shows_the_real_catalog_signatures():
    # If a catalog function's parameters change, the description must change too.
    for name, fn in catalog_functions(CATALOG).items():
        assert signature_text(name, fn) in SEARCH_DESCRIPTION, name


def test_execute_description_shows_the_real_request_signature():
    assert signature_text("request", RequestFunction.__call__) in EXECUTE_DESCRIPTION


def test_examples_are_part_of_their_descriptions():
    assert SEARCH_EXAMPLE in SEARCH_DESCRIPTION
    assert EXECUTE_EXAMPLE in EXECUTE_DESCRIPTION


def test_examples_parse_as_python():
    # Only a syntax check: the sandbox allows top-level await, which ast.parse accepts
    # (compile() would not). Running the examples is the integration test's job.
    import ast

    for example in (SEARCH_EXAMPLE, EXECUTE_EXAMPLE):
        ast.parse(example)


def test_execute_description_states_the_limits_and_the_python_subset():
    assert "At most 50 calls per run" in EXECUTE_DESCRIPTION
    assert "Not available:" in EXECUTE_DESCRIPTION and "class inheritance" in EXECUTE_DESCRIPTION
    assert 'not "+"' in EXECUTE_DESCRIPTION  # the openFDA encoding trap (M1)


def test_instructions_carry_the_disclaimer():
    assert "not medical advice" in INSTRUCTIONS

import json

from openrx.catalog.loader import load_catalog
from openrx.mcp_server.footprint import (
    TOKEN_BUDGET,
    count_tokens,
    per_endpoint_definitions,
    per_endpoint_footprint,
    server_footprint,
    tool_definitions,
)
from openrx.mcp_server.server import build_server

CATALOG = load_catalog()


class NoRunners:
    def run(self, tool, code):
        raise AssertionError("listing tools never runs code")


SERVER = build_server(NoRunners())


def test_tool_definitions_and_instructions_fit_the_budget():
    # docs/design.md NFR-1: both tool definitions + instructions <= 1,500 tokens.
    footprint = server_footprint(SERVER)

    assert footprint.tool_count == 2
    assert footprint.total <= TOKEN_BUDGET, f"{footprint.total} tokens"


def test_counted_definitions_are_what_the_server_sends():
    definitions, instructions = tool_definitions(SERVER)

    assert [d["name"] for d in definitions] == ["search", "execute"]
    assert all({"description", "inputSchema"} <= set(d) for d in definitions)
    assert instructions.startswith("OpenRx:")


def test_title_is_sent_once():
    definitions, _ = tool_definitions(SERVER)

    for d in definitions:
        assert "title" in d and "title" not in d.get("annotations", {})


def test_per_endpoint_baseline_has_one_tool_per_endpoint():
    definitions = per_endpoint_definitions(CATALOG)

    assert len(definitions) == sum(len(s.endpoints) for s in CATALOG) == 23
    event = next(d for d in definitions if d["name"] == "openfda_drug_event")
    assert set(event["inputSchema"]["properties"]) == {"search", "count", "sort", "limit", "skip"}


def test_openrx_is_much_smaller_than_one_tool_per_endpoint():
    # docs/design.md §6.2 reports the ratio; it grows with the catalog, since OpenRx stays flat.
    ours, theirs = server_footprint(SERVER), per_endpoint_footprint(CATALOG)

    assert theirs.total > 5 * ours.total


def test_count_tokens_uses_a_real_tokenizer():
    assert count_tokens("hello world") == 2
    assert count_tokens(json.dumps({"a": 1})) > 1

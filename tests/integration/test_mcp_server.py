"""The MCP server against the deployed runners on Floci. Needs `make up && make deploy`;
run with `make test-integration`.

Two layers: the server in memory wired to the real Lambdas, and `openrx serve` started
as its own process and spoken to over stdio, the way an MCP client or the agent does.
"""

import asyncio
import os
import shutil

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from openrx.mcp_server.descriptions import EXECUTE_EXAMPLE, SEARCH_EXAMPLE
from openrx.mcp_server.server import server_from_environment

pytestmark = pytest.mark.integration

FLOCI_ENV = {
    "AWS_ENDPOINT_URL": "http://127.0.0.1:4566",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
}

SEARCH = "[f\"{h['source']}.{h['id']}\" for h in catalog_find('adverse events', limit=2)]"
EXECUTE = (
    "r = await request('rxnorm', '/rxcui.json', {'name': 'metformin'})\n"
    "r['data']['idGroup']['rxnormId']"
)


def call_both(client_factory):
    async def go():
        async with client_factory() as client:
            search = (await client.call_tool("search", {"code": SEARCH})).structured_content
            execute = (await client.call_tool("execute", {"code": EXECUTE})).structured_content
            # Tools return structured ToolResult dicts, never plain text only.
            assert search is not None and execute is not None
            return search, execute

    return asyncio.run(go())


def test_server_runs_tools_in_the_deployed_runners(monkeypatch):
    for key, value in FLOCI_ENV.items():
        monkeypatch.setenv(key, value)
    server = server_from_environment()

    search, execute = call_both(lambda: Client(server))

    assert search["error"] is None and search["result"][0] == "openfda.drug_event"
    assert execute["error"] is None and execute["result"] == ["6809"]
    assert execute["request_calls"] == 1


def test_openrx_serve_works_over_stdio():
    uv = shutil.which("uv")
    assert uv, "uv must be on PATH"
    transport = StdioTransport(
        command=uv, args=["run", "openrx", "serve"], env={**os.environ, **FLOCI_ENV}
    )

    search, execute = call_both(lambda: Client(transport))

    assert search["result"][0] == "openfda.drug_event"
    assert execute["result"] == ["6809"]


def test_the_examples_in_the_tool_descriptions_work(monkeypatch):
    # The model copies these; they must run and return what they promise.
    for key, value in FLOCI_ENV.items():
        monkeypatch.setenv(key, value)
    server = server_from_environment()

    async def go():
        async with Client(server) as client:
            search = await client.call_tool("search", {"code": SEARCH_EXAMPLE})
            execute = await client.call_tool("execute", {"code": EXECUTE_EXAMPLE})
            return search.structured_content, execute.structured_content

    search, execute = asyncio.run(go())

    assert search is not None and search["error"] is None
    assert search["result"]["path"] == "/drug/event.json"
    assert execute is not None and execute["error"] is None
    assert len(execute["result"]) == 5
    assert all(isinstance(term, str) and count > 0 for term, count in execute["result"])

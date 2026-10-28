import asyncio
import json

from fastmcp import Client

from openrx.agent.mcp_tools import openrx_tools
from openrx.mcp_server.server import build_server
from openrx.runner.results import ToolResult


class FakeRunners:
    def __init__(self):
        self.calls = []

    def run(self, tool, code):
        self.calls.append((tool, code))
        return ToolResult(result=f"{tool}: {code}").to_dict()


def test_server_tools_become_langchain_tools_returning_tool_result_json():
    runners = FakeRunners()

    async def go():
        async with openrx_tools(Client(build_server(runners))) as tools:
            by_name = {t.name: t for t in tools}
            output = await by_name["execute"].ainvoke({"code": "1 + 1"})
            return sorted(by_name), by_name["search"].description, output

    names, search_description, output = asyncio.run(go())

    assert names == ["execute", "search"]
    assert search_description.startswith("Explore the API catalog")
    assert json.loads(output)["result"] == "execute: 1 + 1"
    assert runners.calls == [("execute", "1 + 1")]


def test_tool_schema_takes_only_code():
    async def go():
        async with openrx_tools(Client(build_server(FakeRunners()))) as tools:
            return [t.args for t in tools]

    for args in asyncio.run(go()):
        assert list(args) == ["code"]

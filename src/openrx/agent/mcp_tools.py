"""The OpenRx MCP tools as LangChain tools, through fastmcp's client (D-016).

langchain-mcp-adapters needs the MCP SDK below 2.0, which fastmcp 4 (D-002) has moved
past, so this small adapter takes its place. OpenRx has exactly two tools, each taking
one `code` string, which keeps it simple. Each tool returns the server's ToolResult as
JSON text, the form chat models read tool results in.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import Client
from langchain_core.tools import BaseTool, StructuredTool


@asynccontextmanager
async def openrx_tools(client: Client) -> AsyncIterator[list[BaseTool]]:
    """Connect to the server and yield its tools; the connection stays open inside."""
    async with client:
        listed = await client.list_tools()
        yield [_wrap(client, tool.name, tool.description or "") for tool in listed]


def _wrap(client: Client, name: str, description: str) -> BaseTool:
    async def call(code: str) -> str:
        result = await client.call_tool(name, {"code": code}, raise_on_error=False)
        if result.structured_content is not None:
            return json.dumps(result.structured_content, ensure_ascii=False)
        # Fall back to the text the server sent, e.g. a protocol-level error.
        return "\n".join(getattr(block, "text", "") for block in result.content)

    return StructuredTool.from_function(coroutine=call, name=name, description=description)

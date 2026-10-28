"""The MCP server: search(code) and execute(code), each run in its runner Lambda.

The server itself never runs model code (D-005): it passes the code to the
search-runner or execute-runner Lambda and returns the runner's ToolResult as the tool
result. Anything that goes wrong reaching a runner also comes back as a ToolResult with
an error, never as a crashed session, so the model always gets an answer to act on.

Configuration (environment):

    OPENRX_SEARCH_FUNCTION    search-runner Lambda (default openrx-search-runner)
    OPENRX_EXECUTE_FUNCTION   execute-runner Lambda (default openrx-execute-runner)
    AWS_ENDPOINT_URL          Floci, e.g. http://127.0.0.1:4566 (the Makefile sets it)
"""

import json
import os
from collections.abc import Mapping
from typing import Any, Literal, Protocol, cast

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from openrx.mcp_server.descriptions import (
    EXECUTE_DESCRIPTION,
    INSTRUCTIONS,
    SEARCH_DESCRIPTION,
)
from openrx.runner.requests import LambdaClient
from openrx.runner.results import ToolResult

Tool = Literal["search", "execute"]

SERVER_NAME = "openrx"


class Runners(Protocol):
    """Runs code in a runner and returns its ToolResult dict (a fake in unit tests)."""

    def run(self, tool: Tool, code: str) -> dict[str, Any]:
        """The runner's answer for `code`."""
        ...


class LambdaRunners:
    """The runner Lambdas, invoked with boto3."""

    def __init__(self, client: LambdaClient, functions: Mapping[Tool, str]) -> None:
        """Invoke the Lambda named in `functions` for each tool."""
        self._client = client
        self._functions = dict(functions)

    def run(self, tool: Tool, code: str) -> dict[str, Any]:
        """Invoke the tool's runner; failures to reach it become a ToolResult error."""
        function = self._functions[tool]
        try:
            response = self._client.invoke(
                FunctionName=function, Payload=json.dumps({"code": code}).encode("utf-8")
            )
            body = json.loads(response["Payload"].read())
        except Exception as e:  # noqa: BLE001 - report, never crash the MCP session
            return _unavailable(f"could not reach the {tool} runner: {type(e).__name__}")
        if response.get("FunctionError") or not isinstance(body, dict):
            return _unavailable(f"the {tool} runner failed; try again")
        return body


def _unavailable(message: str) -> dict[str, Any]:
    # Same shape as a runner's ToolResult, so the model reads it like any other result.
    return ToolResult(error={"type": "RunnerUnavailable", "message": message}).to_dict()


def build_server(runners: Runners) -> FastMCP:
    """The MCP server with its two tools, backed by `runners`."""
    mcp = FastMCP(name=SERVER_NAME, instructions=INSTRUCTIONS)

    # Both tools are read-only: OpenRx only ever sends GET requests (catalog schema).
    @mcp.tool(
        description=SEARCH_DESCRIPTION,
        annotations=ToolAnnotations(title="Search the OpenRx API catalog", read_only_hint=True),
    )
    def search(code: str) -> dict[str, Any]:
        return runners.run("search", code)

    @mcp.tool(
        description=EXECUTE_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Run code against FDA/NIH drug APIs", read_only_hint=True, open_world_hint=True
        ),
    )
    def execute(code: str) -> dict[str, Any]:
        return runners.run("execute", code)

    return mcp


def server_from_environment(env: Mapping[str, str] = os.environ) -> FastMCP:
    """The server wired to the runner Lambdas named in the environment."""
    import boto3
    from botocore.config import Config

    # The runners' own limit is 60 s; their Lambda timeout is 90 s.
    config = Config(retries={"max_attempts": 2, "mode": "standard"}, read_timeout=100)
    client = cast(LambdaClient, boto3.client("lambda", config=config))
    functions: dict[Tool, str] = {
        "search": env.get("OPENRX_SEARCH_FUNCTION", "openrx-search-runner"),
        "execute": env.get("OPENRX_EXECUTE_FUNCTION", "openrx-execute-runner"),
    }
    return build_server(LambdaRunners(client, functions))

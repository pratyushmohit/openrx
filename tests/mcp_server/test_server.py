import asyncio
import io
import json
from typing import Any

from fastmcp import Client

from openrx.mcp_server.server import (
    INSTRUCTIONS,
    SERVER_NAME,
    LambdaRunners,
    Tool,
    build_server,
    server_from_environment,
)
from openrx.runner.results import ToolResult


class FakeRunners:
    def __init__(self):
        self.calls = []

    def run(self, tool, code):
        self.calls.append((tool, code))
        return ToolResult(result=f"{tool} ran", duration_ms=1.0).to_dict()


def with_client(server, action):
    async def go():
        async with Client(server) as client:
            return await action(client)

    return asyncio.run(go())


# --- the MCP surface ---------------------------------------------------------------


def test_server_identifies_itself_with_instructions():
    async def info(client):
        # The current MCP handshake (server/discover) exposes these on the client.
        return client.server_info, client.instructions

    server_info, instructions = with_client(build_server(FakeRunners()), info)

    assert server_info.name == SERVER_NAME == "openrx"
    assert instructions == INSTRUCTIONS


def test_exactly_two_tools_each_taking_code():
    async def tools(client):
        return await client.list_tools()

    listed = {t.name: t for t in with_client(build_server(FakeRunners()), tools)}

    assert set(listed) == {"search", "execute"}  # docs/design.md FR-1
    for tool in listed.values():
        assert tool.input_schema["required"] == ["code"]
        assert set(tool.input_schema["properties"]) == {"code"}


def test_tools_are_marked_read_only_and_execute_reaches_out():
    async def tools(client):
        return await client.list_tools()

    listed = {t.name: t.annotations for t in with_client(build_server(FakeRunners()), tools)}

    assert listed["search"].read_only_hint is True and not listed["search"].open_world_hint
    assert listed["execute"].read_only_hint is True and listed["execute"].open_world_hint is True


def test_each_tool_runs_its_own_runner_and_returns_the_tool_result():
    runners = FakeRunners()

    async def call_both(client):
        return [
            (await client.call_tool("search", {"code": "catalog_sources()"})).structured_content,
            (await client.call_tool("execute", {"code": "1"})).structured_content,
        ]

    search_result, execute_result = with_client(build_server(runners), call_both)

    assert runners.calls == [("search", "catalog_sources()"), ("execute", "1")]
    assert search_result["result"] == "search ran" and execute_result["result"] == "execute ran"
    assert set(search_result) == set(ToolResult().to_dict())


# --- reaching the runner Lambdas ----------------------------------------------------


class FakeLambda:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[tuple[str, Any]] = []

    def invoke(self, *, FunctionName: str, Payload: bytes) -> dict[str, Any]:  # noqa: N803
        self.calls.append((FunctionName, json.loads(Payload)))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


FUNCTIONS: dict[Tool, str] = {"search": "openrx-search-runner", "execute": "openrx-execute-runner"}


def payload(body, function_error=None):
    response = {"Payload": io.BytesIO(json.dumps(body).encode())}
    if function_error:
        response["FunctionError"] = function_error
    return response


def test_lambda_runners_invoke_the_right_function_with_the_code():
    client = FakeLambda(payload({"result": 2}))

    answer = LambdaRunners(client, FUNCTIONS).run("execute", "1 + 1")

    assert answer == {"result": 2}
    assert client.calls == [("openrx-execute-runner", {"code": "1 + 1"})]


def test_unreachable_runner_is_a_tool_result_error_not_a_crash():
    answer = LambdaRunners(FakeLambda(ConnectionError("refused")), FUNCTIONS).run("search", "x")

    assert answer["error"] == {
        "type": "RunnerUnavailable",
        "message": "could not reach the search runner: ConnectionError",
    }
    assert set(answer) == set(ToolResult().to_dict())


def test_crashed_runner_is_reported_without_its_internals():
    client = FakeLambda(payload({"errorMessage": "Traceback ..."}, function_error="Unhandled"))

    answer = LambdaRunners(client, FUNCTIONS).run("execute", "x")

    assert answer["error"]["message"] == "the execute runner failed; try again"


def test_server_from_environment_uses_the_configured_functions(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    env = {"OPENRX_SEARCH_FUNCTION": "s-fn", "OPENRX_EXECUTE_FUNCTION": "e-fn"}

    server = server_from_environment(env)

    assert server.name == "openrx"

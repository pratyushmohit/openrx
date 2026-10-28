"""Answer one question end to end: `openrx ask "<question>"` (README FR-12).

The agent talks to the OpenRx MCP server over fastmcp's in-memory transport: the full
MCP protocol, in one process. (`openrx serve` over stdio is the same server; it is
tested separately.) The server runs code in the runner Lambdas on Floci, and the model
is Bedrock on Floci answered by Ollama.
"""

import os
import time
from collections.abc import Mapping
from typing import Any

from fastmcp import Client

from openrx.agent.graph import AgentResult, ask, build_agent
from openrx.agent.mcp_tools import openrx_tools
from openrx.agent.model import make_model
from openrx.mcp_server.server import server_from_environment


async def ask_question(question: str, env: Mapping[str, str] = os.environ) -> AgentResult:
    """Run the agent on `question` against the deployed OpenRx."""
    server = server_from_environment(env)
    async with openrx_tools(Client(server)) as tools:
        agent = build_agent(make_model(env), tools)
        return await ask(agent, question)


def summarise(result: AgentResult, seconds: float) -> dict[str, Any]:
    """Totals over a run's events, for the CLI and the evals (README §6.2)."""
    llm = [e for e in result.events if e["kind"] == "llm"]
    tools = [e for e in result.events if e["kind"] == "tool"]
    fetched = sum(e["bytes_fetched"] for e in tools)
    returned = sum(e["bytes_returned"] for e in tools)
    return {
        "seconds": round(seconds, 1),
        "llm_calls": len(llm),
        "input_tokens": sum(e["input_tokens"] for e in llm),
        "output_tokens": sum(e["output_tokens"] for e in llm),
        "model_seconds": round(sum(e["ms"] for e in llm) / 1000, 1),
        "tool_calls": len(tools),
        "tool_errors": sum(not e["ok"] for e in tools),
        "tool_seconds": round(sum(e["ms"] for e in tools) / 1000, 1),
        "request_calls": sum(e["request_calls"] for e in tools),
        "bytes_fetched": fetched,
        "bytes_returned": returned,
        # README §6.2 data reduction: how much less the model saw than was fetched.
        "data_reduction": round(fetched / returned, 1) if returned else None,
        "stopped_at_limit": result.stopped_at_limit,
    }


def timed(started: float) -> float:
    """Seconds since `started` (a perf_counter value)."""
    return time.perf_counter() - started

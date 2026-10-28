"""The agent loop, built by hand with LangGraph (README §8.7, §9).

    model ──tool calls──► tools ──► model ──no tool calls──► answer
      └── step limit reached (15 model calls) ──► stop with a note

Hand-built rather than a prebuilt ReAct agent so every LLM call and tool call is a
place to measure: each one adds an event (kind, latency, tokens or tool outcome) that
the evals turn into README §6 metrics. All knowledge of the APIs comes from the tools;
the system prompt only describes the way of working.
"""

import json
import operator
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

MAX_STEPS = 15
NO_ANSWER = "(The model finished without a text answer.)"

SYSTEM_PROMPT = """\
You answer questions about drugs with the OpenRx tools. First use search to find the \
right endpoint and read the source's guide, then use execute to fetch the data and \
compute the answer in code. Return small, focused results from code, not whole API \
responses. Base the answer only on data the tools returned, say which source it came \
from, and say plainly when the data doesn't answer the question."""


class AgentState(TypedDict):
    """What flows through the graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    steps: int  # model calls so far
    events: Annotated[list[dict[str, Any]], operator.add]


@dataclass
class AgentResult:
    """The outcome of one question."""

    answer: str
    steps: int
    events: list[dict[str, Any]] = field(default_factory=list)
    stopped_at_limit: bool = False


def build_agent(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    max_steps: int = MAX_STEPS,
    clock: Callable[[], float] = time.perf_counter,
) -> Any:
    """Compile the agent graph for `model` with `tools`."""
    bound = model.bind_tools(list(tools))
    by_name = {tool.name: tool for tool in tools}

    async def call_model(state: AgentState) -> dict[str, Any]:
        started = clock()
        message = await bound.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        usage = getattr(message, "usage_metadata", None) or {}
        event = {
            "kind": "llm",
            "ms": round((clock() - started) * 1000, 1),
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "tool_calls": len(getattr(message, "tool_calls", []) or []),
        }
        return {"messages": [message], "steps": state["steps"] + 1, "events": [event]}

    async def call_tools(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        messages, events = [], []
        for call in getattr(last, "tool_calls", []) or []:
            started = clock()
            tool = by_name.get(call["name"])
            if tool is None:
                output = json.dumps({"error": {"type": "UnknownTool", "message": call["name"]}})
            else:
                output = str(await tool.ainvoke(call["args"]))
            ms = (clock() - started) * 1000
            events.append(_tool_event(call["name"], call["args"], output, ms))
            messages.append(ToolMessage(content=output, tool_call_id=call["id"], name=call["name"]))
        return {"messages": messages, "events": events}

    async def stop_at_limit(state: AgentState) -> dict[str, Any]:
        note = f"Stopped after {max_steps} model calls without a final answer."
        return {"messages": [AIMessage(content=note)], "events": [{"kind": "step_limit"}]}

    def route(state: AgentState) -> str:
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return END
        return "stop_at_limit" if state["steps"] >= max_steps else "tools"

    graph = StateGraph(AgentState)
    graph.add_node("model", call_model)
    graph.add_node("tools", call_tools)
    graph.add_node("stop_at_limit", stop_at_limit)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", route, ["tools", "stop_at_limit", END])
    graph.add_edge("tools", "model")
    graph.add_edge("stop_at_limit", END)
    return graph.compile()


async def ask(agent: Any, question: str) -> AgentResult:
    """Run one question through a compiled agent."""
    state = await agent.ainvoke(
        {"messages": [HumanMessage(question)], "steps": 0, "events": []},
        # LangGraph's own limit counts every node, so give it room beyond max_steps.
        config={"recursion_limit": 4 * MAX_STEPS + 4},
    )
    events = state["events"]
    return AgentResult(
        answer=message_text(state["messages"][-1]) or NO_ANSWER,
        steps=state["steps"],
        events=events,
        stopped_at_limit=any(e["kind"] == "step_limit" for e in events),
    )


def message_text(message: AnyMessage) -> str:
    """The text of a message, whether its content is a string or a list of blocks."""
    content = message.content
    if isinstance(content, str):
        return content.strip()
    parts = [
        block.get("text", "") if isinstance(block, dict) else str(block)
        for block in content
        if not isinstance(block, dict) or block.get("type") == "text"
    ]
    text = "".join(parts).strip()
    if text:
        return text
    # No "text" block (seen once in M4): fall back to any block that carries text,
    # such as a reasoning block, rather than returning an empty answer.
    return "".join(
        str(block.get("text", "")) for block in content if isinstance(block, dict)
    ).strip()


def _tool_event(name: str, args: Any, output: str, ms: float) -> dict[str, Any]:
    try:
        result = json.loads(output)
    except ValueError:
        result = {}
    error = result.get("error") if isinstance(result, dict) else None
    return {
        "kind": "tool",
        "tool": name,
        "ms": round(ms, 1),
        "ok": error is None,
        "error_type": (error or {}).get("type") if isinstance(error, dict) else None,
        "request_calls": result.get("request_calls", 0) if isinstance(result, dict) else 0,
        "bytes_fetched": result.get("bytes_fetched", 0) if isinstance(result, dict) else 0,
        "bytes_returned": result.get("bytes_returned", 0) if isinstance(result, dict) else 0,
        # Kept for the evals: grounding checks and error classes (README §6).
        "args": args,
        "output": output,
    }

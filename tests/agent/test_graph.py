import asyncio
import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from openrx.agent.graph import MAX_STEPS, SYSTEM_PROMPT, ask, build_agent, message_text


class ScriptedModel(BaseChatModel):
    """Plays back a fixed list of replies and records what it was sent."""

    replies: list[AIMessage]
    seen: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":  # type: ignore[override]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(list(messages))
        reply = self.replies.pop(0) if self.replies else self.replies_exhausted()
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def replies_exhausted(self) -> AIMessage:
        raise AssertionError("the agent asked for more replies than scripted")


def tool_call(name: str, code: str, call_id: str = "c1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": {"code": code}, "id": call_id}])


def fake_tool(name: str, result: dict[str, Any], calls: list[str]) -> StructuredTool:
    async def run(code: str) -> str:
        calls.append(code)
        return json.dumps(result)

    return StructuredTool.from_function(coroutine=run, name=name, description=f"{name} tool")


def run_agent(replies, tools=(), max_steps=MAX_STEPS):
    model = ScriptedModel(replies=list(replies), seen=[])
    agent = build_agent(model, list(tools), max_steps=max_steps)
    return asyncio.run(ask(agent, "What are metformin's top adverse events?")), model


def test_direct_answer_without_tools():
    result, model = run_agent([AIMessage(content="I can't tell without data.")])

    assert result.answer == "I can't tell without data."
    assert result.steps == 1 and not result.stopped_at_limit
    assert isinstance(model.seen[0][0], SystemMessage)
    assert model.seen[0][0].content == SYSTEM_PROMPT


def test_search_then_execute_then_answer():
    calls: list[str] = []
    search = fake_tool("search", {"result": ["openfda.drug_event"], "error": None}, calls)
    execute = fake_tool(
        "execute",
        {
            "result": [["NAUSEA", 30159]],
            "error": None,
            "request_calls": 1,
            "bytes_fetched": 900,
            "bytes_returned": 20,
        },
        calls,
    )
    replies = [
        tool_call("search", "catalog_find('adverse events')", "s1"),
        tool_call("execute", "await request(...)", "e1"),
        AIMessage(content="Nausea is the most reported (openFDA FAERS)."),
    ]

    result, model = run_agent(replies, [search, execute])

    assert result.answer == "Nausea is the most reported (openFDA FAERS)."
    assert calls == ["catalog_find('adverse events')", "await request(...)"]
    assert result.steps == 3
    tool_messages = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_messages] == ["s1", "e1"]


def test_events_record_each_llm_and_tool_call():
    calls: list[str] = []
    execute = fake_tool(
        "execute",
        {
            "result": 1,
            "error": None,
            "request_calls": 2,
            "bytes_fetched": 5000,
            "bytes_returned": 10,
        },
        calls,
    )
    first = tool_call("execute", "x")
    first.usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}

    result, _ = run_agent([first, AIMessage(content="done")], [execute])

    kinds = [e["kind"] for e in result.events]
    assert kinds == ["llm", "tool", "llm"]
    llm, tool, _ = result.events
    assert (llm["input_tokens"], llm["output_tokens"], llm["tool_calls"]) == (100, 20, 1)
    assert tool["tool"] == "execute" and tool["ok"] is True
    assert (tool["request_calls"], tool["bytes_fetched"], tool["bytes_returned"]) == (2, 5000, 10)


def test_tool_errors_are_recorded_and_passed_to_the_model():
    calls: list[str] = []
    execute = fake_tool(
        "execute", {"result": None, "error": {"type": "RuntimeError", "message": "x"}}, calls
    )

    result, model = run_agent(
        [tool_call("execute", "bad"), AIMessage(content="it failed")], [execute]
    )

    tool_event = next(e for e in result.events if e["kind"] == "tool")
    assert tool_event["ok"] is False and tool_event["error_type"] == "RuntimeError"
    assert "RuntimeError" in model.seen[-1][-1].content


def test_unknown_tool_name_is_answered_not_crashed():
    result, model = run_agent([tool_call("delete_everything", "x"), AIMessage(content="ok")])

    assert result.answer == "ok"
    assert "UnknownTool" in model.seen[-1][-1].content


def test_step_limit_stops_a_model_that_never_answers():
    calls: list[str] = []
    search = fake_tool("search", {"result": [], "error": None}, calls)
    endless = [tool_call("search", f"try {i}", f"c{i}") for i in range(10)]

    result, _ = run_agent(endless, [search], max_steps=3)

    assert result.stopped_at_limit is True
    assert result.steps == 3 and len(calls) == 2
    assert result.answer == "Stopped after 3 model calls without a final answer."


def test_message_text_reads_strings_and_bedrock_style_blocks():
    assert message_text(AIMessage(content="  plain  ")) == "plain"
    blocks: list[str | dict[Any, Any]] = [
        {"type": "text", "text": "Nausea "},
        {"type": "tool_use", "name": "x"},
        {"type": "text", "text": "leads."},
    ]
    assert message_text(AIMessage(content=blocks)) == "Nausea leads."

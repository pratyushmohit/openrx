from openrx.agent.graph import AgentResult
from openrx.agent.run import summarise

EVENTS = [
    {"kind": "llm", "ms": 2000.0, "input_tokens": 300, "output_tokens": 50, "tool_calls": 1},
    {
        "kind": "tool",
        "tool": "execute",
        "ms": 500.0,
        "ok": True,
        "error_type": None,
        "request_calls": 3,
        "bytes_fetched": 9000,
        "bytes_returned": 300,
    },
    {"kind": "llm", "ms": 1000.0, "input_tokens": 400, "output_tokens": 30, "tool_calls": 0},
]


def test_summarise_totals_the_events():
    totals = summarise(AgentResult(answer="a", steps=2, events=EVENTS), seconds=3.6)

    assert totals["llm_calls"] == 2 and totals["tool_calls"] == 1
    assert (totals["input_tokens"], totals["output_tokens"]) == (700, 80)
    assert (totals["model_seconds"], totals["tool_seconds"]) == (3.0, 0.5)
    assert totals["data_reduction"] == 30.0  # 9000 fetched / 300 returned


def test_summarise_without_tool_calls_has_no_reduction():
    totals = summarise(AgentResult(answer="a", steps=1, events=EVENTS[:1]), seconds=2.0)

    assert totals["data_reduction"] is None

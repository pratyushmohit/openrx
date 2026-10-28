import asyncio
import json

from openrx.agent.graph import AgentResult
from openrx.evals.run import (
    Trial,
    append_line,
    error_codes,
    pending,
    read_runs,
    rescore,
    run_id,
    run_trial,
    trials,
)
from openrx.evals.tasks import load_tasks

SUITE = load_tasks()
TASK = next(t for t in SUITE if t.id == "metformin_reports_2023")


def tool_event(output: dict, ok: bool = True) -> dict:
    return {
        "kind": "tool",
        "tool": "execute",
        "ms": 30.0,
        "ok": ok,
        "error_type": None if ok else "RuntimeError",
        "request_calls": 1,
        "bytes_fetched": 9000,
        "bytes_returned": 30,
        "args": {"code": "..."},
        "output": json.dumps(output),
    }


LLM = {"kind": "llm", "ms": 1000.0, "input_tokens": 900, "output_tokens": 50, "tool_calls": 1}


def agent_returning(answer: str, events: list[dict]):
    async def ask(prompt: str) -> AgentResult:
        assert prompt == TASK.prompt
        return AgentResult(answer=answer, steps=2, events=events)

    return ask


def run(trial, expected, ask, keys=None):
    return asyncio.run(run_trial(trial, expected, ask, "qwen", keys, clock=iter([0, 4.2]).__next__))


def test_trials_interleave_attempts():
    a, b = SUITE[:2]

    got = [(t.task.id, t.attempt) for t in trials([a, b], 2)]

    assert got == [(a.id, 1), (b.id, 1), (a.id, 2), (b.id, 2)]


def test_a_passing_trial_is_scored_and_summarised():
    events = [LLM, tool_event({"result": 26629, "request_calls": 1}), LLM]

    record = run(Trial(TASK, 1), 26629, agent_returning("26,629 reports.", events))

    assert record["passed"] is True and record["score"] == 1.0
    assert (record["task"], record["attempt"], record["model"]) == (TASK.id, 1, "qwen")
    assert (record["grounded_numbers"], record["claimed_numbers"]) == (1, 1)
    assert record["totals"]["llm_calls"] == 2 and record["totals"]["seconds"] == 4.2
    assert record["events"] == events and record["crash"] is None


def test_a_wrong_answer_fails_with_detail():
    record = run(Trial(TASK, 1), 26629, agent_returning("About 100 reports.", [LLM]))

    assert record["passed"] is False and record["detail"] == "expected 26629"


def test_error_codes_from_tool_outputs_are_kept():
    bad = tool_event({"error": {"type": "RuntimeError", "message": "unknown_path: no /x"}}, False)

    record = run(Trial(TASK, 1), 26629, agent_returning("26629", [LLM, bad, LLM]))

    assert record["error_codes"] == ["unknown_path"]


def test_new_fixtures_are_the_keys_added_during_the_trial():
    snapshots = iter([{"fixtures/a.json"}, {"fixtures/a.json", "fixtures/b.json"}])

    record = run(Trial(TASK, 1), 26629, agent_returning("26629", []), keys=snapshots.__next__)

    assert record["new_fixtures"] == ["fixtures/b.json"]


def test_an_agent_crash_is_recorded_not_raised():
    async def broken(prompt: str) -> AgentResult:
        raise ConnectionError("ollama is down")

    record = run(Trial(TASK, 2), 26629, broken)

    assert record["passed"] is False and record["answer"] is None
    assert record["crash"] == "ConnectionError: ollama is down"


def test_error_codes():
    def out(message):
        return json.dumps({"error": {"type": "RuntimeError", "message": message}})

    assert error_codes(out("budget_exceeded: used all 50")) == ["budget_exceeded"]
    assert error_codes(out("name 'x' is not defined")) == []
    assert error_codes(json.dumps({"result": 1})) == []
    assert error_codes("not json") == []


def test_run_id_is_sortable_and_file_safe():
    assert run_id("us.meta/llama:3", now=0).endswith("-us.meta-llama-3")


def test_runs_file_round_trip(tmp_path):
    path = tmp_path / "runs" / "r.jsonl"

    append_line(path, {"task": "a"})
    append_line(path, {"task": "b"})

    assert [r["task"] for r in read_runs(path)] == ["a", "b"]


def test_rescore_uses_the_current_scorer():
    record = run(Trial(TASK, 1), 26629, agent_returning("26,629 reports.", [LLM]))
    stale = {**record, "passed": False, "score": 0.0, "detail": "old scorer"}

    fresh = rescore(stale, TASK)

    assert (fresh["passed"], fresh["score"], fresh["detail"]) == (True, 1.0, "")


def test_rescore_leaves_crashed_trials_alone():
    crashed = {"answer": None, "passed": False}

    assert rescore(crashed, TASK) is crashed


def test_pending_skips_trials_already_in_the_runs_file():
    a, b = SUITE[:2]
    done = [{"task": a.id, "attempt": 1}, {"task": b.id, "attempt": 1}]

    left = pending(trials([a, b], 2), done)

    assert [(t.task.id, t.attempt) for t in left] == [(a.id, 2), (b.id, 2)]

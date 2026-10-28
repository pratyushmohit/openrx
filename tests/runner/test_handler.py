import json

import pytest

pytest.importorskip("pydantic_monty")  # handler imports the sandbox module

from openrx.runner import handler as handler_module  # noqa: E402
from openrx.runner.handler import MAX_CODE_CHARS, Runner  # noqa: E402
from openrx.runner.results import ToolResult  # noqa: E402
from openrx.runner.sandbox import Limits  # noqa: E402


class FakeHost:
    """Records what the handler asked for instead of running Monty (D-005)."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result or ToolResult(result=2, duration_ms=1.5)

    def run(self, code, functions, type_check=True, limits=None):
        self.calls.append(
            {"code": code, "functions": functions, "type_check": type_check, "limits": limits}
        )
        return self.result


def make_runner(result=None):
    host, logs = FakeHost(result), []
    runner = Runner("search", host, {"catalog_find": lambda text: []}, log=logs.append)
    return runner, host, logs


def test_code_is_run_with_the_runners_functions():
    runner, host, _ = make_runner()

    answer = runner.handle({"code": "1 + 1"})

    assert answer["result"] == 2 and answer["error"] is None
    [call] = host.calls
    assert call["code"] == "1 + 1" and call["type_check"] is True
    assert set(call["functions"]) == {"catalog_find"}
    assert call["limits"] == Limits()


def test_type_check_can_be_turned_off():
    runner, host, _ = make_runner()

    runner.handle({"code": "x", "type_check": False})

    assert host.calls[0]["type_check"] is False


def test_max_seconds_can_only_lower_the_limit():
    runner, host, _ = make_runner()

    runner.handle({"code": "x", "max_seconds": 2})
    runner.handle({"code": "x", "max_seconds": 3600})

    assert [c["limits"].seconds for c in host.calls] == [2.0, Limits().seconds]


@pytest.mark.parametrize(
    ("event", "message"),
    [
        ("print(1)", "must be an object"),
        ({}, "'code' must be a non-empty string"),
        ({"code": "   "}, "'code' must be a non-empty string"),
        ({"code": 42}, "'code' must be a non-empty string"),
        ({"code": "x" * (MAX_CODE_CHARS + 1)}, "longer than"),
        ({"code": "x", "type_check": "yes"}, "'type_check' must be true or false"),
        ({"code": "x", "max_seconds": 0}, "positive number"),
        ({"code": "x", "max_seconds": True}, "positive number"),
        ({"code": "x", "max_seconds": "10"}, "positive number"),
    ],
)
def test_malformed_events_come_back_as_bad_request(event, message):
    runner, host, _ = make_runner()

    answer = runner.handle(event)

    assert answer["error"]["type"] == "BadRequest"
    assert message in answer["error"]["message"]
    assert host.calls == []  # nothing ran


def test_answer_has_the_tool_result_shape():
    runner, _, _ = make_runner()

    assert set(runner.handle({"code": "1"})) == set(ToolResult().to_dict())


def test_one_json_log_line_per_run():
    failed = ToolResult(error={"type": "ZeroDivisionError", "message": "division by zero"})
    runner, _, logs = make_runner(failed)

    runner.handle({"code": "1/0"})

    [line] = logs
    assert json.loads(line) == {
        "event": "search_run",
        "ok": False,
        "error_type": "ZeroDivisionError",
        "duration_ms": 0.0,
        "bytes_returned": 0,
        "truncated": False,
    }


def test_search_handler_builds_its_runner_once(monkeypatch):
    built = []

    def fake_runner():
        runner, _, _ = make_runner()
        built.append(runner)
        return runner

    monkeypatch.setattr(handler_module, "_search", None)
    monkeypatch.setattr(handler_module, "search_runner", fake_runner)

    handler_module.search_handler({"code": "1"}, None)
    handler_module.search_handler({"code": "2"}, None)

    assert len(built) == 1

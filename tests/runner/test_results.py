import datetime
import json

import pytest

from openrx.runner.results import (
    MAX_RESULT_CHARS,
    MAX_STDOUT_CHARS,
    ToolResult,
    shape_result,
    to_json,
)

# --- to_json ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [None, True, 0, -5, 1.5, "text", "", [1, "a"], {"a": {"b": [None]}}]
)
def test_json_values_pass_through(value):
    assert to_json(value) == value


def test_tuples_become_lists():
    assert to_json((1, (2, 3))) == [1, [2, 3]]


def test_sets_become_sorted_lists_so_the_order_is_stable():
    assert to_json({3, 1, 2}) == [1, 2, 3]
    assert to_json(frozenset({"b", "a"})) == ["a", "b"]
    assert to_json({1, "a", None}) == to_json({None, "a", 1})  # mixed types too


@pytest.mark.parametrize(
    ("key", "expected"),
    [(1, "1"), (2.5, "2.5"), (True, "true"), (None, "null"), ((1, 2), "(1, 2)")],
)
def test_dict_keys_become_strings(key, expected):
    assert to_json({key: "v"}) == {expected: "v"}


def test_non_finite_floats_become_text():
    assert to_json([float("nan"), float("inf"), float("-inf")]) == ["nan", "inf", "-inf"]


def test_dates_become_iso_text():
    assert to_json(datetime.date(2026, 10, 28)) == "2026-10-28"
    assert to_json(datetime.datetime(2026, 10, 28, 9, 30)) == "2026-10-28T09:30:00"


def test_bytes_and_other_objects():
    class Thing:
        def __repr__(self):
            return "<Thing>"

    assert to_json(b"caf\xc3\xa9") == "café"
    assert to_json(Thing()) == "<Thing>"


def test_very_deep_nesting_is_cut_off():
    deep = []
    node = deep
    for _ in range(200):
        node.append([])
        node = node[0]

    # Would raise RecursionError if not cut off.
    text = json.dumps(to_json(deep), ensure_ascii=False)

    assert "…" in text


def test_converted_values_always_serialise():
    weird = {(1, 2): {3, 4}, 5: (float("nan"), datetime.date(2026, 1, 1), b"x")}

    json.dumps(to_json(weird))  # no exception


# --- shape_result -------------------------------------------------------------------


def test_small_result_is_returned_as_is():
    shaped = shape_result({"top": ["NAUSEA", "VOMITING"]}, stdout="done\n")

    assert isinstance(shaped, ToolResult)
    assert shaped.result == {"top": ["NAUSEA", "VOMITING"]}
    assert shaped.stdout == "done\n"
    assert shaped.truncated is False and shaped.notes == []
    assert shaped.bytes_returned == len('{"top":["NAUSEA","VOMITING"]}')


def test_large_result_is_shrunk_to_fit_but_keeps_its_shape():
    raw = {"results": [{"id": i, "text": "x" * 500, "tags": ["a"] * 50} for i in range(500)]}

    shaped = shape_result(raw)

    assert shaped.truncated is True
    assert len(json.dumps(shaped.result)) <= MAX_RESULT_CHARS
    first = shaped.result["results"][0]
    assert set(first) == {"id", "text", "tags"}  # field names survive
    assert "was" in shaped.notes[0] and "filter" in shaped.notes[0]


def test_result_too_wide_to_shrink_falls_back_to_a_text_preview():
    wide = {f"key_{i}": i for i in range(20_000)}  # trimming keeps every key

    shaped = shape_result(wide)

    assert shaped.truncated is True
    assert isinstance(shaped.result, str) and len(shaped.result) == MAX_RESULT_CHARS
    assert shaped.result.endswith("…")


def test_bytes_returned_counts_what_is_actually_returned():
    shaped = shape_result(["é" * 10])

    assert shaped.bytes_returned == len(json.dumps(["é" * 10], ensure_ascii=False).encode())


def test_long_stdout_is_cut_with_a_note():
    shaped = shape_result(None, stdout="y" * (MAX_STDOUT_CHARS + 5))

    assert len(shaped.stdout) == MAX_STDOUT_CHARS + 1 and shaped.stdout.endswith("…")
    assert "printed output was cut" in shaped.notes[0]
    assert shaped.truncated is False  # the result itself was fine


def test_tool_result_has_the_readme_shape():
    keys = set(ToolResult().to_dict())

    # docs/design.md §8.4: {result, stdout, error, traceback, type_errors, truncated,
    # duration_ms, request_calls, bytes_fetched, bytes_returned}, plus notes.
    assert keys == {
        "result",
        "stdout",
        "error",
        "traceback",
        "type_errors",
        "truncated",
        "duration_ms",
        "request_calls",
        "bytes_fetched",
        "bytes_returned",
        "notes",
    }

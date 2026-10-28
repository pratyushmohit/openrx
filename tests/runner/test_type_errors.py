import json

import pytest

pydantic_monty = pytest.importorskip("pydantic_monty")  # sandbox imports it

from openrx.runner.sandbox import parse_type_errors  # noqa: E402


@pytest.mark.parametrize(
    "diagnostic",
    [
        {"code": "invalid-key", "message": "m", "location": {"row": 3, "column": 7}},
        {"code": "invalid-key", "message": "m", "location": {"start": {"line": 3, "column": 7}}},
        {"code": "invalid-key", "message": "m", "range": {"start": {"line": 3, "column": 7}}},
        {
            "check_name": "invalid-key",
            "description": "m",
            "location": {"positions": {"begin": {"line": 3, "column": 7}}},
        },
    ],
)
def test_positions_are_found_whatever_the_layout(diagnostic):
    [error] = parse_type_errors(json.dumps([diagnostic]))

    assert error == {"line": 3, "column": 7, "code": "invalid-key", "message": "m"}


def test_wrapped_diagnostics_list():
    rendered = json.dumps(
        {"diagnostics": [{"code": "c", "message": "m", "location": {"row": 1, "column": 1}}]}
    )

    assert parse_type_errors(rendered)[0]["line"] == 1


def test_unexpected_output_is_kept_as_raw_text():
    assert parse_type_errors("error: something odd") == [{"message": "error: something odd"}]
    assert parse_type_errors("42") == [{"message": "42"}]
    assert parse_type_errors('["plain"]') == [{"message": "plain"}]


def test_missing_position_is_none():
    [error] = parse_type_errors(json.dumps([{"code": "c", "message": "m"}]))

    assert (error["line"], error["column"]) == (None, None)

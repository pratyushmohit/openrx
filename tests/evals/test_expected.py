import json

import pytest

from openrx.evals.expected import load_expected, save_expected, shape_problem
from openrx.evals.fixtures import FIXTURES_DIR
from openrx.evals.tasks import Task, load_tasks

SUITE = load_tasks()


def task(kind: str) -> Task:
    return Task.model_validate(
        {
            "id": "t",
            "category": "single_lookup",
            "difficulty": "easy",
            "prompt": "A question long enough.",
            "sources": ["rxnorm"],
            "scoring": {"type": kind},
            "reference": "1",
        }
    )


# --- the committed expected answers ----------------------------------------------


def test_every_task_has_an_expected_answer_of_the_right_shape():
    expected = load_expected()

    assert set(expected) == {t.id for t in SUITE}
    problems = [f"{t.id}: {p}" for t in SUITE if (p := shape_problem(t, expected[t.id]))]
    assert problems == []


def test_fixtures_for_the_suite_are_committed():
    sources = {s for t in SUITE for s in t.sources}

    assert {p.name for p in FIXTURES_DIR.iterdir() if p.is_dir()} >= sources


def test_committed_fixture_requests_hold_only_placeholders():
    # The request part is ours: the contact email and any key must be placeholders.
    for path in FIXTURES_DIR.glob("*/*.json"):
        request = json.loads(path.read_text(encoding="utf-8"))["request"]
        for name, value in {**request["fixed"], **request["params"]}.items():
            if name in ("email", "api_key", "tool"):
                assert name == "tool" or str(value).startswith("${"), (path.name, name)


# --- shape checks ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("contains_all", ["a"]),
        ("ordered", ["a", "b"]),
        ("number", 3),
        ("number", 1.5),
        ("boolean", False),
    ],
)
def test_good_shapes(kind, value):
    assert shape_problem(task(kind), value) is None


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("contains_all", []),
        ("ordered", ["a", ""]),
        ("contains_all", "a"),
        ("number", True),
        ("number", "3"),
        ("boolean", 0),
        ("boolean", None),
    ],
)
def test_bad_shapes(kind, value):
    assert shape_problem(task(kind), value) is not None


def test_save_and_load_round_trip_sorted_with_lf(tmp_path):
    path = tmp_path / "expected.json"

    save_expected({"b": 2, "a": ["x"]}, path)

    assert load_expected(path) == {"a": ["x"], "b": 2}
    raw = path.read_bytes()
    assert b"\r\n" not in raw and raw.index(b'"a"') < raw.index(b'"b"')

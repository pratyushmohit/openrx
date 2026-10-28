from collections import Counter

import pytest
import yaml

from openrx.catalog.loader import load_catalog
from openrx.evals.tasks import TaskError, lint_tasks, load_tasks

CATALOG = load_catalog()
SUITE = load_tasks()

VALID = {
    "id": "metformin_rxcui",
    "category": "single_lookup",
    "difficulty": "easy",
    "prompt": "What is the RxNorm RxCUI for metformin?",
    "sources": ["rxnorm"],
    "scoring": {"type": "contains_all"},
    "reference": 'r = await request("rxnorm", "/rxcui.json", {"name": "metformin"})\nr',
}


def write(tmp_path, tasks):
    path = tmp_path / "tasks.yaml"
    path.write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    return path


# --- the shipped suite --------------------------------------------------------------


def test_shipped_suite_loads_and_lints_clean():
    assert len(SUITE) == 25
    assert lint_tasks(SUITE, CATALOG) == []


def test_shipped_suite_covers_every_category():
    counts = Counter(t.category for t in SUITE)

    assert counts == {
        "single_lookup": 5,
        "chained": 6,
        "fan_out": 5,
        "aggregation": 5,
        "discovery": 4,
    }


def test_shipped_suite_uses_every_source_and_scoring_type():
    assert {s for t in SUITE for s in t.sources} == {s.id for s in CATALOG}
    assert {t.scoring.type for t in SUITE} == {"contains_all", "ordered", "number", "boolean"}


# --- loading ------------------------------------------------------------------------


def test_a_valid_task_loads(tmp_path):
    [task] = load_tasks(write(tmp_path, [VALID]))

    assert task.id == "metformin_rxcui" and task.scoring.tolerance == 0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"category": "trivia"}, "category"),
        ({"scoring": {"type": "vibes"}}, "scoring.type"),
        ({"scoring": {"type": "number", "tolerance": -1}}, "tolerance"),
        ({"reference": "def f(:"}, "not valid Python"),
        ({"sources": []}, "sources"),
        ({"prompt": "short"}, "prompt"),
        ({"id": "Bad-Id"}, "id"),
        ({"extra": 1}, "extra"),
    ],
)
def test_invalid_tasks_are_reported(tmp_path, change, message):
    with pytest.raises(TaskError) as excinfo:
        load_tasks(write(tmp_path, [{**VALID, **change}]))

    assert message in str(excinfo.value)


def test_file_without_a_tasks_list(tmp_path):
    path = tmp_path / "tasks.yaml"
    path.write_text("- just a list\n", encoding="utf-8")

    with pytest.raises(TaskError, match="tasks:"):
        load_tasks(path)


# --- lint ---------------------------------------------------------------------------


def test_lint_catches_duplicates_unknown_sources_and_unused_sources(tmp_path):
    tasks = load_tasks(
        write(
            tmp_path,
            [VALID, VALID, {**VALID, "id": "other", "sources": ["rxnorm", "pubmed", "nope"]}],
        )
    )

    problems = lint_tasks(tasks, CATALOG)

    assert "duplicate task id 'metformin_rxcui'" in problems
    assert "other: unknown sources ['nope']" in problems
    assert "other: reference never requests from 'pubmed'" in problems

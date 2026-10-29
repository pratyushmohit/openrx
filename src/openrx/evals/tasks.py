"""The eval task suite: schema, loader and lint (docs/design.md §12.1).

Tasks live in evals/tasks.yaml. Each has a prompt for the agent and reference code: a
plain, readable solution in the same sandbox Python the agent writes. Running the
reference in the execute-runner against the recorded fixtures gives the expected
answer, so expected values come from the same data the agent sees and are never typed
in by hand. The reference returns the shape its scoring rule expects:

    contains_all  list of strings   every item must appear in the answer
    ordered       list of strings   items must appear, in this order (a ranking)
    number        int or float      the number must appear, within `tolerance`
    boolean       true or false     the answer must say yes or no accordingly
"""

import ast
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from openrx.catalog.loader import Catalog, read_yaml

TASKS_FILE = Path(__file__).parents[3] / "evals" / "tasks.yaml"

Category = Literal["single_lookup", "chained", "fan_out", "aggregation", "discovery"]
ScoringType = Literal["contains_all", "ordered", "number", "boolean"]


class Scoring(BaseModel):
    """How an answer is checked against the reference's result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ScoringType
    # For `number`: the largest allowed absolute difference (0 means exact).
    tolerance: float = Field(default=0.0, ge=0)


class Task(BaseModel):
    """One eval task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    category: Category
    difficulty: Literal["easy", "medium", "hard"]
    prompt: str = Field(min_length=10)
    sources: tuple[str, ...] = Field(min_length=1)
    scoring: Scoring
    reference: str = Field(min_length=1)

    @field_validator("reference")
    @classmethod
    def _reference_parses(cls, code: str) -> str:
        # ast.parse accepts top-level await, which the sandbox allows.
        try:
            ast.parse(code)
        except SyntaxError as e:
            raise ValueError(f"reference is not valid Python: {e.msg} (line {e.lineno})") from None
        return code


class TaskError(Exception):
    """The task file is invalid; `problems` lists what is wrong."""

    def __init__(self, problems: list[str]) -> None:
        """Create the error from a list of problems."""
        self.problems = problems
        super().__init__("\n".join(problems))


def load_tasks(path: Path = TASKS_FILE) -> list[Task]:
    """Load and validate the task suite.

    Raises:
        TaskError: if the file or any task is invalid.
    """
    data = read_yaml(path)
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise TaskError([f"{path.name}: expected a top-level 'tasks:' list"])
    tasks, problems = [], []
    for index, raw in enumerate(data["tasks"]):
        name = raw.get("id", f"#{index}") if isinstance(raw, dict) else f"#{index}"
        try:
            tasks.append(Task.model_validate(raw))
        except ValidationError as e:
            problems += [
                f"{name}: {'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()
            ]
    if problems:
        raise TaskError(problems)
    return tasks


def lint_tasks(tasks: list[Task], catalog: Catalog) -> list[str]:
    """Checks across tasks: unique ids, real sources, and references that use them."""
    problems = []
    counts = Counter(t.id for t in tasks)
    problems += [f"duplicate task id {i!r}" for i, n in counts.items() if n > 1]
    known = {s.id for s in catalog}
    for task in tasks:
        if unknown := sorted(set(task.sources) - known):
            problems.append(f"{task.id}: unknown sources {unknown}")
        for source in task.sources:
            if f'"{source}"' not in task.reference and f"'{source}'" not in task.reference:
                problems.append(f"{task.id}: reference never requests from {source!r}")
    return problems

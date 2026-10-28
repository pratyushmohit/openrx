"""Expected answers: what each task's reference returned on the recorded data (§12.1).

`openrx eval record` runs every reference and saves the results to evals/expected.json
(committed, next to the fixtures they came from), so a reviewer sees the expected values
and a change in them shows up in the diff. Values are never typed in by hand.
"""

import json
from pathlib import Path
from typing import Any

from openrx.evals.tasks import Task

EXPECTED_FILE = Path(__file__).parents[3] / "evals" / "expected.json"


def shape_problem(task: Task, value: Any) -> str | None:
    """Why `value` can't be the expected answer for `task`, or None if it can."""
    kind = task.scoring.type
    if kind in ("contains_all", "ordered"):
        if not isinstance(value, list) or not value:
            return f"{kind} needs a non-empty list, got {value!r:.80}"
        if not all(isinstance(item, str) and item.strip() for item in value):
            return f"{kind} needs a list of non-empty strings, got {value!r:.80}"
        return None
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            return f"number needs an int or float, got {value!r:.80}"
        return None
    if not isinstance(value, bool):
        return f"boolean needs true or false, got {value!r:.80}"
    return None


def save_expected(values: dict[str, Any], path: Path = EXPECTED_FILE) -> None:
    """Write the expected answers, sorted by task id, with LF endings."""
    text = json.dumps(dict(sorted(values.items())), indent=2, ensure_ascii=False) + "\n"
    path.write_bytes(text.encode("utf-8"))


def load_expected(path: Path = EXPECTED_FILE) -> dict[str, Any]:
    """The expected answers by task id."""
    return json.loads(path.read_text(encoding="utf-8"))

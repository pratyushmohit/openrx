"""Run the agent on every task, k times, and record what happened (README §12.2).

Each trial is one call to the agent (`openrx ask`, the same code path) with the bridge
in replay_or_record mode: data the reference already recorded is replayed, and anything
new the agent asks for is fetched once and recorded too, so later runs replay it
(D-013). Every trial becomes one JSON line in results/runs/<run id>.jsonl with the
answer, its score, the totals from `summarise`, and every model and tool event, so the
report (and any later question) can be answered from the file alone.
"""

import json
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openrx.agent.graph import AgentResult
from openrx.agent.run import summarise
from openrx.evals.scoring import grounding, score_answer
from openrx.evals.tasks import Task

RUNS_DIR = Path(__file__).parents[3] / "results" / "runs"

# Bridge error codes that mean the model called an endpoint or source that doesn't
# exist in the catalog: the "hallucinated endpoint" metric (README §6.1).
HALLUCINATION_CODES = ("unknown_source", "unknown_path")

Ask = Callable[[str], Awaitable[AgentResult]]


@dataclass(frozen=True)
class Trial:
    """One task, one attempt."""

    task: Task
    attempt: int  # 1..k


def trials(tasks: Iterable[Task], k: int) -> list[Trial]:
    """Every task k times, attempt by attempt (all tasks once, then again ...).

    Interleaving keeps a slow or failing stretch (e.g. Ollama warming up) from landing
    on one task's attempts only.
    """
    tasks = list(tasks)
    return [Trial(task, attempt) for attempt in range(1, k + 1) for task in tasks]


def error_codes(output: str) -> list[str]:
    """Bridge and sandbox error codes named in a tool's output ("unknown_path: ...")."""
    try:
        result = json.loads(output)
    except ValueError:
        return []
    error = result.get("error") if isinstance(result, dict) else None
    if not isinstance(error, dict):
        return []
    message = str(error.get("message", ""))
    code = message.split(":", 1)[0].strip()
    return [code] if code and code.replace("_", "").isalpha() and code.islower() else []


def record_trial(
    trial: Trial,
    expected: Any,
    result: AgentResult | None,
    seconds: float,
    model: str,
    new_fixtures: list[str],
    crash: str | None = None,
) -> dict[str, Any]:
    """The JSON line for one trial."""
    task = trial.task
    base = {
        "task": task.id,
        "category": task.category,
        "difficulty": task.difficulty,
        "attempt": trial.attempt,
        "model": model,
        "expected": expected,
        "new_fixtures": new_fixtures,
    }
    if result is None:  # the agent itself failed (Ollama down, ...): an infra error
        return {
            **base,
            "answer": None,
            "passed": False,
            "score": 0.0,
            "crash": crash,
            "totals": {"seconds": round(seconds, 1)},
            "events": [],
        }
    score = score_answer(result.answer, expected, task.scoring)
    outputs = [e["output"] for e in result.events if e["kind"] == "tool"]
    grounded, claims = grounding(result.answer, outputs)
    codes = [c for out in outputs for c in error_codes(out)]
    return {
        **base,
        "answer": result.answer,
        "passed": score.passed,
        "score": round(score.score, 3),
        "detail": score.detail,
        "grounded_numbers": grounded,
        "claimed_numbers": claims,
        "error_codes": codes,
        "crash": None,
        "totals": summarise(result, seconds),
        "events": result.events,
    }


async def run_trial(
    trial: Trial,
    expected: Any,
    ask: Ask,
    model: str,
    fixture_keys: Callable[[], set[str]] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Ask the agent one task's prompt and score the answer."""
    before = fixture_keys() if fixture_keys else set()
    started = clock()
    result, crash = None, None
    try:
        result = await ask(trial.task.prompt)
    except Exception as e:  # noqa: BLE001 - one broken trial must not end the run
        crash = f"{type(e).__name__}: {e}"[:500]
    seconds = clock() - started
    new = sorted(fixture_keys() - before) if fixture_keys else []
    return record_trial(trial, expected, result, seconds, model, new, crash)


def run_id(model: str, now: float | None = None) -> str:
    """A sortable, file-safe id: 20261028-153000-openrx-local."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    safe = "".join(c if c.isalnum() or c in "-." else "-" for c in model)
    return f"{stamp}-{safe}"


def append_line(path: Path, record: dict[str, Any]) -> None:
    """Append one record as a JSON line (written as it finishes, so a crash keeps it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_runs(path: Path) -> list[dict[str, Any]]:
    """Every trial in a runs file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]

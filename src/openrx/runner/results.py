"""Turn what sandboxed code produced into the tool result the model sees (docs/design.md §8.4).

Only this result enters the model's context, so it is capped (FR-8): about 10k tokens,
measured as 40,000 characters of JSON because the Lambda has no tokenizer (~4 chars per
token; M5 measures real token counts). An oversized result is shrunk step by step
(fewer list items, shorter strings) until it fits, keeping its shape so the model can
see what it got and filter in code next time.
"""

import datetime
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

from openrx.catalog.trim import trim

MAX_RESULT_CHARS = 40_000
MAX_STDOUT_CHARS = 10_000
_MAX_DEPTH = 64

# Each step keeps fewer items and shorter strings; the first that fits is used.
_SHRINK_STEPS = [(100, 1000), (50, 500), (20, 200), (10, 100), (5, 80), (2, 60), (1, 40)]

TRUNCATED_HINT = (
    "the result was too large and was shortened; return less (filter, aggregate, "
    "count, or pick fields in code) instead of whole API responses"
)


@dataclass
class ToolResult:
    """What `search` and `execute` return to the model (docs/design.md §8.4)."""

    result: Any = None
    stdout: str = ""
    error: dict[str, Any] | None = None
    traceback: str | None = None
    type_errors: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    duration_ms: float = 0.0
    request_calls: int = 0
    bytes_fetched: int = 0
    bytes_returned: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Plain-data form for the Lambda response."""
        return asdict(self)


def to_json(value: Any, _depth: int = 0) -> Any:
    """Convert a sandbox value to JSON-safe data.

    Tuples become lists, sets become sorted lists (a stable order), dict keys become
    strings, dates become ISO text, NaN and infinity become text, and anything else
    becomes its repr(). Very deep nesting is cut off with "…".
    """
    if _depth > _MAX_DEPTH:
        return "…"
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)  # "nan", "inf", "-inf"
    if isinstance(value, dict):
        return {_key(k): to_json(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(v, _depth + 1) for v in value]
    if isinstance(value, set | frozenset):
        items = [to_json(v, _depth + 1) for v in value]
        return sorted(items, key=lambda v: (type(v).__name__, json.dumps(v, sort_keys=True)))
    if isinstance(value, datetime.date | datetime.time):
        return value.isoformat()  # datetime is a date subclass
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return repr(value)


def _key(key: Any) -> str:
    if isinstance(key, str):
        return key
    if key is None or isinstance(key, bool | int | float):
        return json.dumps(key)  # True -> "true", 1 -> "1", None -> "null"
    return repr(key)


def shape_result(value: Any, stdout: str = "") -> ToolResult:
    """Build the result part of a ToolResult, capped to fit the model's context."""
    data = to_json(value)
    text = _dumps(data)
    result = ToolResult(result=data, bytes_returned=len(text.encode("utf-8")))

    if len(text) > MAX_RESULT_CHARS:
        result.result = _shrink(data)
        result.truncated = True
        result.notes.append(f"{TRUNCATED_HINT} (was {len(text):,} characters)")
        result.bytes_returned = len(_dumps(result.result).encode("utf-8"))

    if len(stdout) > MAX_STDOUT_CHARS:
        result.stdout = stdout[:MAX_STDOUT_CHARS] + "…"
        result.notes.append(f"printed output was cut to {MAX_STDOUT_CHARS:,} characters")
    else:
        result.stdout = stdout
    return result


def _shrink(data: Any) -> Any:
    for max_items, max_string in _SHRINK_STEPS:
        candidate = trim(data, max_items=max_items, max_string=max_string)
        if len(_dumps(candidate)) <= MAX_RESULT_CHARS:
            return candidate
    # Still too big, e.g. a dict with thousands of keys: fall back to a text preview.
    return _dumps(data)[: MAX_RESULT_CHARS - 1] + "…"


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

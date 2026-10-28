"""Deterministic scoring of an agent's answer against a task's expected value (§12.2).

Answers are free text, expected values come from the data, so matching is lenient in
the ways a correct answer can differ: case, punctuation, British vs American spelling
("DIARRHOEA" vs "Diarrhea"), and company suffixes ("Novo Nordisk A/S" vs "Novo
Nordisk"). Each check gives a pass/fail and a partial score in [0, 1] (README §6.1).

Grounding (README §6.1) is checked separately: the share of numbers in the answer that
appear in data the tools returned.
"""

import re
from dataclasses import dataclass
from typing import Any

from openrx.evals.tasks import Scoring

# British -> American spellings common in MedDRA terms and labels.
_SPELLINGS = {
    "diarrhoea": "diarrhea",
    "oedema": "edema",
    "anaemia": "anemia",
    "haemorrhage": "hemorrhage",
    "haematoma": "hematoma",
    "oesophag": "esophag",
    "leukaemia": "leukemia",
    "paediatric": "pediatric",
    "tumour": "tumor",
    "haem": "hem",
}
_COMPANY_SUFFIXES = {
    "inc",
    "llc",
    "ltd",
    "a s",
    "as",
    "co",
    "corp",
    "corporation",
    "company",
    "gmbh",
    "plc",
    "sa",
    "ag",
    "limited",
    "and company",
}
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class Score:
    """How an answer did on one task."""

    passed: bool
    score: float  # partial credit in [0, 1]
    detail: str = ""


def normalize(text: str) -> str:
    """Lowercase, American spelling, punctuation as spaces, single spaces."""
    text = text.lower()
    for british, american in _SPELLINGS.items():
        text = text.replace(british, american)
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    text = re.sub(r"\.(?!\d)", " ", text)  # keep decimal points only
    return " ".join(text.split())


def normalize_item(item: str) -> str:
    """An expected item, normalized, with any trailing company suffix dropped."""
    words = normalize(item).split()
    while len(words) > 1:
        for size in (2, 1):
            if " ".join(words[-size:]) in _COMPANY_SUFFIXES and len(words) > size:
                words = words[:-size]
                break
        else:
            break
    return " ".join(words)


def _find(item: str, answer: str) -> int:
    """Position of `item` in `answer` as whole words (a plural is fine), or -1."""
    words = [re.escape(word) + "(?:s|es)?" for word in normalize_item(item).split()]
    pattern = rf"(?<![a-z0-9]){' '.join(words)}(?![a-z0-9])"
    match = re.search(pattern, answer)
    return match.start() if match else -1


def numbers_in(text: str) -> list[float]:
    """Every number written in `text` ("26,629", "12.7%", "-3")."""
    values = []
    for raw in _NUMBER.findall(text):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return values


def score_answer(answer: str, expected: Any, scoring: Scoring) -> Score:
    """Check `answer` against `expected` using the task's scoring rule."""
    if scoring.type == "number":
        return _score_number(answer, float(expected), scoring.tolerance)
    if scoring.type == "boolean":
        return _score_boolean(answer, bool(expected))
    items = [str(i) for i in expected]
    text = normalize(answer)
    positions = [_find(item, text) for item in items]
    missing = [i for i, p in zip(items, positions, strict=True) if p < 0]
    if scoring.type == "contains_all":
        found = len(items) - len(missing)
        return Score(not missing, found / len(items) if items else 1.0, _missing(missing))
    # ordered: every item present, in order. Partial: the longest in-order run of the
    # items that were found, as a share of all items.
    in_order = _longest_increasing([p for p in positions if p >= 0])
    passed = not missing and in_order == len(items)
    detail = _missing(missing) or ("" if passed else "out of order")
    return Score(passed, in_order / len(items) if items else 1.0, detail)


def _score_number(answer: str, expected: float, tolerance: float) -> Score:
    found = numbers_in(answer)
    hit = any(abs(n - expected) <= tolerance + 1e-9 for n in found)
    return Score(hit, 1.0 if hit else 0.0, "" if hit else f"expected {expected:g}")


_NO = re.compile(
    r"\b(no|not|none|isn t|is not|does not|doesn t|there is no|there are no|without)\b"
)
_YES = re.compile(r"\b(yes|does|is listed|there is|there are|has|have|carries)\b")


def _score_boolean(answer: str, expected: bool) -> Score:
    text = normalize(answer)
    first = text.split()[0] if text else ""
    if first in ("yes", "no"):
        said: bool | None = first == "yes"
    elif _NO.search(text):
        said = False
    elif _YES.search(text):
        said = True
    else:
        said = None
    passed = said is expected
    return Score(passed, 1.0 if passed else 0.0, "" if passed else f"expected {expected}")


def grounding(answer: str, tool_outputs: list[str]) -> tuple[int, int]:
    """(grounded, total) numbers of 2+ digits in the answer found in the tool outputs.

    A deterministic check for numbers and ids (README §6.1); prose grounding needs the
    deferred LLM judge.
    """
    data = {n for output in tool_outputs for n in numbers_in(output)}
    claims = [n for n in numbers_in(answer) if abs(n) >= 10]
    grounded = sum(1 for n in claims if n in data)
    return grounded, len(claims)


def _missing(items: list[str]) -> str:
    return f"missing {items}" if items else ""


def _longest_increasing(values: list[int]) -> int:
    best: list[int] = []
    for v in values:
        lo, hi = 0, len(best)
        while lo < hi:
            mid = (lo + hi) // 2
            if best[mid] < v:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(best):
            best.append(v)
        else:
            best[lo] = v
    return len(best)
